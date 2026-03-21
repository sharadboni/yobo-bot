"""Agent entry point: WebSocket client that connects to the gateway."""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import websockets
from agent.config import WS_URL
from agent.graph import build_graph
from agent.services.user_store import approve_user, ignore_user, normalize_jid
from agent.services.llm import synthesize_speech
from agent.services.scheduler import run_scheduler, set_send_fn, register_handler
from agent.skills.schedule import _handle_news, _handle_search, _handle_podcast
from agent.sanitize import sanitize_user_input, sanitize_llm_output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("agent")

graph = build_graph()
admin_jid: str = ""
last_pending_number: str = ""  # last number from admin_notify, for bare /add or /ignore


async def handle_message(ws, payload: dict):
    """Process an inbound WhatsApp message through the LangGraph pipeline."""
    global admin_jid, last_pending_number

    msg_type = payload.get("type")

    # Admin JID announcement from gateway
    if msg_type == "admin_jid":
        admin_jid = payload["jid"]
        log.info("Admin JID set: %s", admin_jid)
        return

    if msg_type != "message":
        log.warning("Unknown payload type: %s", msg_type)
        return

    sender = normalize_jid(payload.get("from", ""))
    content = payload.get("content", {})
    text = sanitize_user_input(content.get("text", ""))

    log.info("Message from=%s admin_jid=%s is_admin=%s text=%r",
             sender, admin_jid, sender == admin_jid or _is_same_user(sender, admin_jid), text)

    # Admin commands: /add and /ignore
    if sender == admin_jid or _is_same_user(sender, admin_jid):
        cmd = text.strip().lower()

        if cmd.startswith("/add"):
            arg = text.strip()[4:].strip().lstrip("+")
            number = arg if arg else last_pending_number
            if not number:
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /add <number>"}})
                return
            if approve_user(number):
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": f"User {number} approved."}})
                if number == last_pending_number:
                    last_pending_number = ""
            else:
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": f"User {number} not found."}})
            return

        if cmd == "/clear" or cmd.startswith("/clear "):
            arg = text.strip()[6:].strip()
            await _send(ws, {"type": "clear_chats", "target": arg or "all"})
            await _send(ws, {"type": "reply", "to": sender, "content": {"text": f"Clearing chats: {arg or 'all'}..."}})
            return

        if cmd.startswith("/ignore"):
            arg = text.strip()[7:].strip().lstrip("+")
            number = arg if arg else last_pending_number
            if not number:
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /ignore <number>"}})
                return
            if ignore_user(number):
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": f"User {number} will be ignored."}})
                if number == last_pending_number:
                    last_pending_number = ""
            else:
                await _send(ws, {"type": "reply", "to": sender, "content": {"text": f"User {number} not found."}})
            return

    # Run pipeline
    initial_state = {
        "inbound": payload,
        "user_jid": sender,
        "push_name": payload.get("pushName", ""),
        "admin_jid": admin_jid,
        "messages": [],
        "user_profile": {},
        "resolved_text": "",
        "content_type": "",
        "intent": "",
        "intent_args": "",
        "reply_text": "",
        "reply_audio": "",
        "reply_audio_mimetype": "",
        "outbound": [],
    }

    result = await graph.ainvoke(initial_state)

    # Send any outbound messages (admin notifications etc.)
    for out_msg in result.get("outbound", []):
        await _send(ws, out_msg)
        # Track the last pending number for bare /add or /ignore
        if out_msg.get("type") == "admin_notify":
            from agent.services.user_store import jid_to_number
            last_pending_number = jid_to_number(sender)

    # Send reply to user
    reply = result.get("reply_text", "")
    log.info("Pipeline result: intent=%s reply_text=%r content_type=%s",
             result.get("intent"), reply[:200] if reply else "", result.get("content_type"))
    # Sanitize LLM output — redact leaked JIDs, paths, keys
    if reply:
        reply = sanitize_llm_output(reply, user_jid=sender)
    if reply:
        reply_msg = {
            "type": "reply",
            "to": sender,
            "content": {"text": reply},
        }

        # Generate TTS if input was audio or skill requests it (e.g. podcast)
        if result.get("content_type") == "audio" or result.get("intent") == "podcast":
            try:
                audio_bytes, mimetype = await synthesize_speech(reply)
                reply_msg["content"]["audio"] = base64.b64encode(audio_bytes).decode()
                reply_msg["content"]["audio_mimetype"] = mimetype
            except Exception as e:
                log.warning("TTS failed, sending text only: %s", e)

        await _send(ws, reply_msg)


def _is_same_user(jid1: str, jid2: str) -> bool:
    """Compare JIDs ignoring device suffix."""
    return jid1.split(":")[0].split("@")[0] == jid2.split(":")[0].split("@")[0]


async def _send(ws, msg: dict):
    await ws.send(json.dumps(msg))


async def main():
    log.info("Agent starting, connecting to %s", WS_URL)

    # Register scheduler task handlers
    register_handler("news", _handle_news)
    register_handler("search", _handle_search)
    register_handler("podcast", _handle_podcast)

    # Start scheduler in background
    scheduler_task = asyncio.create_task(run_scheduler())

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                log.info("Connected to gateway")
                # Give scheduler access to send messages
                set_send_fn(lambda msg: _send(ws, msg))

                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                        await handle_message(ws, payload)
                    except Exception as e:
                        log.error("Error handling message: %s", e, exc_info=True)
        except (ConnectionRefusedError, websockets.exceptions.ConnectionClosed) as e:
            log.warning("Connection lost (%s), reconnecting in 3s...", e)
            await asyncio.sleep(3)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
