"""Normalize input: text passthrough, audio→STT, image→vision description.
Also handles pending voice clone intercept."""
from __future__ import annotations
import logging
import base64
from agent.services.llm import transcribe_audio, vision_completion
from agent.services.voice_store import (
    get_pending_voice, clear_pending_voice,
    add_custom_voice, set_active_voice,
)

log = logging.getLogger(__name__)


async def resolve_input_node(state: dict) -> dict:
    content = state["inbound"].get("content", {})
    ctype = content.get("type", "text")
    user_jid = state.get("user_jid", "")

    if ctype == "text":
        return {
            "resolved_text": content.get("text", ""),
            "content_type": "text",
        }

    if ctype == "audio":
        # Check for pending voice clone first
        pending = get_pending_voice(user_jid)
        if pending:
            audio_b64 = content.get("data", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                add_custom_voice(user_jid, pending["name"], audio_bytes, pending.get("ref_text", ""))
                set_active_voice(user_jid, pending["name"])
                clear_pending_voice(user_jid)
                return {
                    "resolved_text": "",
                    "content_type": "audio",
                    "reply_text": f"Voice *{pending['name']}* saved and set as active!",
                    "intent": "__voice_clone__",
                }

        # Normal audio → STT
        try:
            audio_bytes = base64.b64decode(content["data"])
            text = await transcribe_audio(audio_bytes, content.get("mimetype", "audio/ogg"))
            return {"resolved_text": text, "content_type": "audio"}
        except Exception as e:
            log.error("STT failed: %s", e, exc_info=True)
            return {
                "resolved_text": "",
                "content_type": "audio",
                "reply_text": "Sorry, I couldn't process that audio.",
                "intent": "__error__",
            }

    if ctype == "image":
        caption = content.get("caption", "")
        try:
            img_b64 = content["data"]
            prompt = caption or "Describe this image and respond helpfully."
            description = await vision_completion(img_b64, prompt)
            return {"resolved_text": description, "content_type": "image"}
        except Exception as e:
            log.error("Vision failed: %s", e, exc_info=True)
            return {
                "resolved_text": caption,
                "content_type": "image",
                "reply_text": "Sorry, I couldn't process that image.",
                "intent": "__error__",
            }

    return {"resolved_text": str(content), "content_type": "unknown"}
