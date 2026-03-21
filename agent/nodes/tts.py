"""TTS node — generate audio reply when needed."""
from __future__ import annotations
import base64
import logging
from agent.services.llm import synthesize_speech
from agent.services.voice_store import get_active_voice
from agent.sanitize import sanitize_llm_output

log = logging.getLogger(__name__)


async def tts_node(state: dict) -> dict:
    """Generate TTS audio if the input was audio or the skill requests it."""
    reply = state.get("reply_text", "")
    intent = state.get("intent", "")
    content_type = state.get("content_type", "")
    user_jid = state.get("user_jid", "")

    # Skip for non-audio intents, errors, pending, etc.
    if not reply:
        return {}
    if intent.startswith("__"):
        return {}
    if content_type != "audio" and intent != "podcast":
        return {}

    # Sanitize output before TTS
    reply = sanitize_llm_output(reply, user_jid=user_jid)

    try:
        voice = get_active_voice(user_jid)
        log.info("TTS: user=%s voice=%s cloned=%s", user_jid, voice["name"], bool(voice["ref_audio_b64"]))
        audio_bytes, mimetype = await synthesize_speech(
            reply,
            voice_name=voice["name"],
            ref_audio_b64=voice["ref_audio_b64"],
            ref_text=voice["ref_text"],
        )
        return {
            "reply_audio": base64.b64encode(audio_bytes).decode(),
            "reply_audio_mimetype": mimetype,
            "reply_text": reply,  # pass sanitized version
        }
    except Exception as e:
        log.warning("TTS failed, sending text only: %s", e)
        return {"reply_text": reply}
