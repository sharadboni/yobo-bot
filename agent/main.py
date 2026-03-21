"""Agent entry point: WebSocket client, message dispatch, scheduler bootstrap."""
from __future__ import annotations
import asyncio
import json
import logging
import websockets
from agent.config import WS_URL
from agent.graph import build_graph
from agent.admin import AdminState, handle_admin_command
from agent.sanitize import sanitize_user_input, sanitize_llm_output
from agent.jid import normalize_jid
from agent.services.scheduler import run_scheduler, register_handler, outbound_queue
from agent.services.task_handlers import handle_news, handle_search, handle_podcast

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

    sender = normalize_jid(payload.get("from", ""))
    content = payload.get("content", {})
    text = sanitize_user_input(content.get("text", ""))

    log.info("Message from=%s is_admin=%s text=%r", sender, admin.is_admin(sender), text)

    # Admin commands — handled outside the pipeline
    if admin.is_admin(sender):
        handled = await handle_admin_command(send_fn, admin, sender, text)
        if handled:
            return

    # Keep typing indicator alive during pipeline execution
    async def keep_typing():
        while True:
            await asyncio.sleep(20)
            try:
                await send_fn({"type": "typing", "to": sender})
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())

    try:
        result = await graph.ainvoke({
            "inbound": payload,
            "user_jid": sender,
            "push_name": payload.get("pushName", ""),
            "admin_jid": admin.admin_jid,
            "user_profile": {},
            "resolved_text": "",
            "content_type": "",
            "intent": "",
            "intent_args": "",
            "reply_text": "",
            "reply_audio": "",
            "reply_audio_mimetype": "",
            "outbound": [],
        })
    finally:
        typing_task.cancel()

    # Send outbound messages (admin notifications etc.)
    for out_msg in result.get("outbound", []):
        await send_fn(out_msg)
        if out_msg.get("type") == "admin_notify":
            admin.track_pending(sender)

    # Send reply
    reply = result.get("reply_text", "")
    if reply:
        reply = sanitize_llm_output(reply, user_jid=sender)
    if reply:
        reply_msg = {
            "type": "reply",
            "to": sender,
            "content": {"text": reply},
        }
        # Attach TTS audio if the pipeline generated it
        if result.get("reply_audio"):
            reply_msg["content"]["audio"] = result["reply_audio"]
            reply_msg["content"]["audio_mimetype"] = result.get("reply_audio_mimetype", "audio/ogg")
        await send_fn(reply_msg)

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

    asyncio.create_task(run_scheduler())

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                log.info("Connected to gateway")
                send_fn = lambda msg: _send(ws, msg)

                # Drain scheduler outbound queue in background
                async def drain_queue():
                    while True:
                        msg = await outbound_queue.get()
                        try:
                            await send_fn(msg)
                        except Exception as e:
                            log.warning("Failed to send scheduled message: %s", e)

                drain_task = asyncio.create_task(drain_queue())

                try:
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                            # Process messages concurrently — don't block on long operations
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
