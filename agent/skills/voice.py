"""Voice management skill — set, add, remove, and list TTS voices."""
from __future__ import annotations
import logging
from agent.services.voice_store import (
    set_active_voice, add_custom_voice, remove_custom_voice,
    list_voices, set_pending_voice, BUILTIN_VOICES,
)

log = logging.getLogger(__name__)


async def voice_cmd(state: dict) -> dict:
    """Handle /voice command.

    /voice              — show current voice and usage
    /voice list         — list all voices
    /voice set <name>   — set active voice
    /voice add <name> [transcript] — start adding a custom voice (step 1)
    /voice remove <name> — remove a custom voice
    """
    args = state.get("intent_args", "").strip()
    user_jid = state.get("user_jid", "")

    if not args:
        voices = list_voices(user_jid)
        return {"reply_text": (
            f"Current voice: *{voices['active']}*\n\n"
            "Usage:\n"
            "/voice list — show all voices\n"
            "/voice set <name> — change your voice\n"
            "/voice add <name> <transcript> — add a custom voice\n"
            "/voice remove <name> — remove a custom voice"
        )}

    parts = args.split(None, 1)
    subcmd = parts[0].lower()
    subargs = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "list":
        voices = list_voices(user_jid)
        lines = [f"Active: *{voices['active']}*\n"]
        lines.append("Built-in voices:")
        for v in voices["builtin"]:
            marker = " (active)" if v == voices["active"] else ""
            lines.append(f"  - {v}{marker}")
        if voices["custom"]:
            lines.append("\nCustom voices:")
            for v in voices["custom"]:
                marker = " (active)" if v == voices["active"] else ""
                lines.append(f"  - {v}{marker}")
        return {"reply_text": "\n".join(lines)}

    if subcmd == "set":
        if not subargs:
            return {"reply_text": "Usage: /voice set <name>"}
        name = subargs
        if not set_active_voice(user_jid, name):
            # Try matching with underscores/case stripped (WhatsApp often strips underscores)
            normalized = name.lower().replace("_", "").replace(" ", "")
            for b in BUILTIN_VOICES:
                if b.lower().replace("_", "") == normalized:
                    name = b
                    break
            if not set_active_voice(user_jid, name):
                return {"reply_text": f"Voice '{subargs}' not found. Use /voice list to see available voices."}
        return {"reply_text": f"Voice set to *{name}*."}

    if subcmd == "add":
        if not subargs:
            return {"reply_text": (
                "Usage: /voice add <name> <transcript>\n\n"
                "Example:\n"
                "  /voice add myvoice Hello, this is what my voice sounds like\n\n"
                "Then send a voice note saying exactly that transcript."
            )}

        add_parts = subargs.split(None, 1)
        voice_name = add_parts[0]
        ref_text = add_parts[1] if len(add_parts) > 1 else ""

        # Step 1: store pending, wait for audio
        set_pending_voice(user_jid, voice_name, ref_text)

        msg = f"Ready to add voice *{voice_name}*.\n\nNow send a voice note"
        if ref_text:
            msg += f" saying:\n\"{ref_text}\""
        else:
            msg += ". For best results, include a transcript:\n/voice add {voice_name} <what you'll say>"
        return {"reply_text": msg}

    if subcmd == "remove":
        if not subargs:
            return {"reply_text": "Usage: /voice remove <name>"}
        if remove_custom_voice(user_jid, subargs):
            return {"reply_text": f"Voice *{subargs}* removed."}
        return {"reply_text": f"Custom voice '{subargs}' not found."}

    return {"reply_text": "Unknown subcommand. Use /voice for help."}
