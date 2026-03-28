"""TTS node — generate audio reply when needed.

This is the ONLY place markdown stripping happens for TTS.
The LLM is allowed to output markdown freely — this node cleans it
before passing to speech synthesis. This is by design: don't fight
the LLM's training, just post-process reliably.
"""
from __future__ import annotations
import base64
import logging
from agent.services.llm import synthesize_dialogue, synthesize_speech
from agent.services.voice_store import get_active_voice
from agent.sanitize import sanitize_llm_output, strip_markdown

log = logging.getLogger(__name__)


async def tts_node(state: dict) -> dict:
    """Generate TTS audio if the input was audio or the skill requests it."""
    reply = state.get("reply_text", "")
    intent = state.get("intent", "")
    content_type = state.get("content_type", "")
    user_jid = state.get("user_jid", "")

    if not reply:
        log.warning("TTS skipped: empty reply_text for intent=%s content_type=%s", intent, content_type)
        return {}
    if intent.startswith("__"):
        return {}
    if content_type not in ("audio", "dialogue") and intent != "podcast" and not state.get("force_audio"):
        return {}

    try:
        # Dialogue mode: multi-voice synthesis
        if content_type == "dialogue" and state.get("dialogue_segments"):
            segments = state["dialogue_segments"]
            log.info("TTS dialogue: user=%s segments=%d intent=%s", user_jid, len(segments), intent)
            audio_bytes, mimetype = await synthesize_dialogue(segments)
            return {
                "reply_audio": base64.b64encode(audio_bytes).decode(),
                "reply_audio_mimetype": mimetype,
                "reply_text": reply,
            }

        # Single voice mode
        tts_text = sanitize_llm_output(reply, user_jid=user_jid)
        tts_text = strip_markdown(tts_text)

        voice = get_active_voice(user_jid)
        log.info("TTS: user=%s voice=%s cloned=%s chars=%d intent=%s",
                 user_jid, voice["name"], bool(voice["ref_audio_b64"]), len(tts_text), intent)
        audio_bytes, mimetype = await synthesize_speech(
            tts_text,
            voice_name=voice["name"],
            ref_audio_b64=voice["ref_audio_b64"],
            ref_text=voice["ref_text"],
        )
        return {
            "reply_audio": base64.b64encode(audio_bytes).decode(),
            "reply_audio_mimetype": mimetype,
            "reply_text": reply,
        }
    except Exception as e:
        log.warning("TTS failed, sending text only: %s", e)
        return {"reply_text": reply}
