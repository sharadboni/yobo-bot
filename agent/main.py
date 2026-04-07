"""Agent entry point: WebSocket client, message dispatch, scheduler bootstrap."""
from __future__ import annotations
import asyncio
import json
import logging
import websockets
from agent.config import WS_URL
from agent.graph import build_graph
from agent.admin import AdminState, handle_admin_command
from agent.sanitize import sanitize_user_input, sanitize_llm_output, markdown_to_whatsapp
from agent.jid import normalize_jid, is_group_jid
from agent.services.scheduler import run_scheduler, register_handler, message_queue
from agent.services.task_handlers import handle_news, handle_search, handle_podcast, handle_webhook
from agent.services.voice_store import refresh_voices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("agent")

graph = build_graph()
admin = AdminState()


async def handle_message(send_fn, payload: dict):
    """Process an inbound WhatsApp message through the LangGraph pipeline."""
    msg_type = payload.get("type")

    if msg_type == "admin_jid":
        admin.admin_jid = payload["jid"]
        log.info("Admin JID set: %s", admin.admin_jid)
        return

    if msg_type != "message":
        return

    # Determine identity and group context
    is_group = payload.get("isGroup", False)
    sender_name = payload.get("pushName", "")
    if is_group:
        # Group is the profile identity; participant is just metadata
        user_jid = payload.get("from", "")       # group JID = profile identity
        sender_jid = normalize_jid(payload.get("participant", ""))
        group_jid = user_jid
        chat_jid = group_jid
        push_name = payload.get("groupName", "")  # use group name for profile
    else:
        user_jid = normalize_jid(payload.get("from", ""))
        sender_jid = user_jid
        group_jid = ""
        chat_jid = user_jid
        push_name = sender_name

    content = payload.get("content", {})
    text = sanitize_user_input(content.get("text", ""))

    log.info("Message from=%s sender=%s group=%s is_admin=%s text=%r",
             user_jid, f"{sender_name} ({sender_jid})" if is_group else "DM",
             group_jid or "n/a", admin.is_admin(sender_jid), text)

    # Admin commands — handled outside the pipeline (DMs only)
    if not is_group and admin.is_admin(sender_jid):
        handled = await handle_admin_command(send_fn, admin, sender_jid, text)
        if handled:
            return

    # Keep typing indicator alive during pipeline execution
    async def keep_typing():
        while True:
            await asyncio.sleep(20)
            try:
                await send_fn({"type": "typing", "to": chat_jid})
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())

    try:
        result = await graph.ainvoke({
            "inbound": payload,
            "user_jid": user_jid,
            "sender_jid": sender_jid,
            "push_name": push_name,
            "admin_jid": admin.admin_jid,
            "is_group": is_group,
            "group_jid": group_jid,
            "sender_name": sender_name,
            "user_profile": {},
            "resolved_text": "",
            "content_type": "",
            "intent": "",
            "intent_args": "",
            "reply_text": "",
            "reply_audio": "",
            "reply_audio_mimetype": "",
            "dialogue_segments": [],
            "force_audio": payload.get("force_audio", False),
            "scheduled": payload.get("scheduled", False),
            "outbound": [],
        })
    finally:
        typing_task.cancel()

    # Send outbound messages (admin notifications etc.)
    for out_msg in result.get("outbound", []):
        await send_fn(out_msg)
        if out_msg.get("type") == "admin_notify":
            admin.track_pending(user_jid)

    # Send reply
    reply = result.get("reply_text", "")
    if payload.get("scheduled") and reply:
        topic = payload.get("content", {}).get("text", "").lstrip("/news ").lstrip("/search ").lstrip("/podcast ")
        reply = f"Scheduled update — {topic}:\n\n{reply}" if reply else reply
    if reply:
        reply = sanitize_llm_output(reply, user_jid=user_jid)
        reply = markdown_to_whatsapp(reply)
    if reply:
        # In groups, prefix reply with sender's name for clarity
        if is_group:
            push_name = payload.get("pushName", "")
            if push_name:
                reply = f"@{push_name}\n{reply}"

        reply_msg = {
            "type": "reply",
            "to": chat_jid,
            "content": {"text": reply},
        }
        # Quote the original message in group chats
        if is_group and payload.get("quotedMsgKey"):
            reply_msg["quoted"] = payload["quotedMsgKey"]
        # Attach TTS audio if the pipeline generated it
        if result.get("reply_audio"):
            reply_msg["content"]["audio"] = result["reply_audio"]
            reply_msg["content"]["audio_mimetype"] = result.get("reply_audio_mimetype", "audio/ogg")
            if result.get("audio_only"):
                reply_msg["content"]["audio_only"] = True
        await send_fn(reply_msg)

    # Clear typing indicator
    try:
        await send_fn({"type": "typing_stop", "to": chat_jid})
    except Exception:
        pass

    log.info("Pipeline result: intent=%s content_type=%s has_audio=%s",
             result.get("intent"), result.get("content_type"), bool(result.get("reply_audio")))


_send_lock = asyncio.Lock()


def _on_task_done(task: asyncio.Task):
    """Log exceptions from background message handler tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Message handler failed: %s", exc, exc_info=exc)


async def _send(ws, msg: dict):
    async with _send_lock:
        await ws.send(json.dumps(msg))


async def main():
    log.info("Agent starting, connecting to %s", WS_URL)

    # Register scheduler task handlers
    register_handler("news", handle_news)
    register_handler("search", handle_search)
    register_handler("podcast", handle_podcast)
    register_handler("webhook", handle_webhook)

    # Fetch available voices from TTS server
    await refresh_voices()

    asyncio.create_task(run_scheduler())

    while True:
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,   # send ping every 20s
                ping_timeout=10,    # wait 10s for pong before closing
                close_timeout=5,
            ) as ws:
                log.info("Connected to gateway")
                send_fn = lambda msg: _send(ws, msg)

                # Drain scheduler message queue — run synthetic messages through the pipeline
                async def drain_queue():
                    while True:
                        payload = await message_queue.get()
                        log.info("Processing scheduled message for %s", payload.get("from"))
                        try:
                            task = asyncio.create_task(handle_message(send_fn, payload))
                            task.add_done_callback(_on_task_done)
                        except Exception as e:
                            log.warning("Failed to process scheduled message: %s", e)

                drain_task = asyncio.create_task(drain_queue())

                try:
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                            task = asyncio.create_task(handle_message(send_fn, payload))
                            task.add_done_callback(_on_task_done)
                        except Exception as e:
                            log.error("Error handling message: %s", e, exc_info=True)
                finally:
                    drain_task.cancel()
        except (ConnectionRefusedError, websockets.exceptions.ConnectionClosed) as e:
            log.warning("Connection lost (%s), reconnecting in 3s...", e)
            await asyncio.sleep(3)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
