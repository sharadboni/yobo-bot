"""Normalize input: text passthrough, audio→STT, image→vision description."""
from __future__ import annotations
import logging
import base64
from agent.services.llm import transcribe_audio, vision_completion

log = logging.getLogger(__name__)


async def resolve_input_node(state: dict) -> dict:
    content = state["inbound"].get("content", {})
    ctype = content.get("type", "text")

    if ctype == "text":
        return {
            "resolved_text": content.get("text", ""),
            "content_type": "text",
        }

    if ctype == "audio":
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
