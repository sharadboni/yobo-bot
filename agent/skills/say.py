"""Say skill — convert text directly to speech, no LLM processing."""
from __future__ import annotations


async def say(state: dict) -> dict:
    text = state.get("intent_args", "").strip()
    if not text:
        return {"reply_text": "Usage: /say <text to speak>"}
    return {"reply_text": text, "content_type": "audio"}
