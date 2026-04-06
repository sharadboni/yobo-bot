"""Voice management skill — set, add, remove, and list TTS voices."""
from __future__ import annotations
from collections import defaultdict
import logging
import re
from agent.services.voice_store import (
    set_active_voice, set_dialogue_host, set_dialogue_guest,
    add_custom_voice, remove_custom_voice,
    list_voices, set_pending_voice, get_voice_names, voice_by_number,
    custom_voice_by_number,
)

log = logging.getLogger(__name__)

_CUSTOM_NUM_RE = re.compile(r"^c(\d+)$", re.IGNORECASE)


def _display_name(voice_id: str) -> str:
    """Extract a friendly display name from a backend voice ID.

    Kokoro:    af_heart    → Heart
    VibeVoice: en-Emma_woman → Emma
    """
    # VibeVoice: lang-Name_gender
    if "-" in voice_id and "_" in voice_id:
        _, rest = voice_id.split("-", 1)
        return rest.rsplit("_", 1)[0]
    # Kokoro: prefix_name  (af_heart, zm_yunyang)
    if "_" in voice_id:
        return voice_id.split("_", 1)[1].capitalize()
    return voice_id


def _display_active(voice_id: str) -> str:
    """Friendly name for showing in status line."""
    return f"{_display_name(voice_id)} ({voice_id})"


def _format_voice_list(voices: list[dict], active: str, model: str) -> list[str]:
    """Build a numbered, language-grouped voice list with friendly names."""
    by_lang: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    names = get_voice_names(model)
    for v in voices:
        idx = names.index(v["name"]) + 1 if v["name"] in names else 0
        lang = v.get("language_name", "Other")
        by_lang[lang].append((idx, v))

    lines: list[str] = []
    for lang, entries in by_lang.items():
        lines.append(f"\n  {lang}")
        for idx, v in entries:
            vid = v["name"]
            friendly = _display_name(vid)
            gender = v.get("gender", "")
            g_icon = "F" if gender == "female" else "M" if gender == "male" else ""
            marker = " <<" if vid == active else ""
            lines.append(f"    {idx}. {friendly} ({g_icon}){marker}")
    return lines


def _resolve_any_voice(input_str: str, user_jid: str, model: str) -> str | None:
    """Resolve user input to a voice name.

    Tries in order: c<N> custom number, builtin number, exact ID, friendly name, fuzzy, custom.
    """
    # c1, c2, ... → custom voice by number
    m = _CUSTOM_NUM_RE.match(input_str)
    if m:
        return custom_voice_by_number(user_jid, int(m.group(1)))

    # Builtin number
    if input_str.isdigit():
        return voice_by_number(model, int(input_str))

    names = get_voice_names(model)

    # Exact builtin ID
    if input_str in names:
        return input_str

    # Match by friendly display name (e.g. "Heart" → af_heart, "Emma" → en-Emma_woman)
    input_lower = input_str.lower()
    for v in names:
        if _display_name(v).lower() == input_lower:
            return v

    # Fuzzy match on full ID (strips underscores/hyphens/spaces)
    normalized = input_lower.replace("_", "").replace("-", "").replace(" ", "")
    for v in names:
        if v.lower().replace("_", "").replace("-", "") == normalized:
            return v

    # Custom voice by name (exact then fuzzy)
    voices = list_voices(user_jid)
    for v in voices["custom"]:
        if v == input_str:
            return v
    for v in voices["custom"]:
        if v.lower().replace("_", "").replace("-", "") == normalized:
            return v

    return None


async def voice_cmd(state: dict) -> dict:
    """Handle /voice command."""
    args = state.get("intent_args", "").strip()
    user_jid = state.get("user_jid", "")

    if not args:
        voices = list_voices(user_jid)
        return {"reply_text": (
            f"Single: *{_display_active(voices['active'])}*\n"
            f"Duo host: *{_display_active(voices['dialogue_host'])}*\n"
            f"Duo guest: *{_display_active(voices['dialogue_guest'])}*\n\n"
            "Usage:\n"
            "/voice list — show all voices\n"
            "/voice set single <#|c#|name>\n"
            "/voice set duo <host> <guest>\n"
            "/voice set duo host <#|c#|name>\n"
            "/voice set duo guest <#|name>\n"
            "/voice add <name> <transcript> — clone voice\n"
            "/voice remove <name|c#>"
        )}

    parts = args.split()
    subcmd = parts[0].lower()

    # --- LIST ---
    if subcmd == "list":
        voices = list_voices(user_jid)
        lines = [
            f"Single: *{_display_active(voices['active'])}*",
            f"Duo host: *{_display_active(voices['dialogue_host'])}*",
            f"Duo guest: *{_display_active(voices['dialogue_guest'])}*",
        ]

        lines.append("\n*Kokoro* — single TTS (/voice set single #)")
        lines.extend(_format_voice_list(voices["kokoro"], voices["active"], "kokoro"))

        lines.append("\n*VibeVoice* — duo/podcast (/voice set duo #)")
        lines.extend(_format_voice_list(
            voices["vibevoice"],
            voices["dialogue_host"],
            "vibevoice",
        ))

        if voices["custom"]:
            lines.append("\n*Custom* — single or duo host (/voice set single c# or /voice set duo host c#)")
            for i, v in enumerate(sorted(voices["custom"]), 1):
                markers = []
                if v == voices["active"]:
                    markers.append("single")
                if v == voices["dialogue_host"]:
                    markers.append("duo host")
                marker = f" << {', '.join(markers)}" if markers else ""
                lines.append(f"    c{i}. {v}{marker}")

        return {"reply_text": "\n".join(lines)}

    # --- SET ---
    if subcmd == "set":
        if len(parts) < 2:
            return {"reply_text": (
                "Usage:\n"
                "/voice set single <#|c#|name>\n"
                "/voice set duo <host#> <guest#>\n"
                "/voice set duo host <#|c#|name>\n"
                "/voice set duo guest <#|name>"
            )}
        mode = parts[1].lower()
        rest = parts[2:]

        if mode == "single":
            return _set_single(user_jid, rest)
        elif mode == "duo":
            return _set_duo(user_jid, rest)
        else:
            # Shorthand: /voice set <value> → single
            return _set_single(user_jid, parts[1:])

    # --- ADD ---
    if subcmd == "add":
        subargs = args.split(None, 1)[1].strip() if len(parts) > 1 else ""
        if not subargs:
            return {"reply_text": (
                "Usage: /voice add <name> <transcript>\n\n"
                "Example:\n"
                "  /voice add myvoice Hello, this is what my voice sounds like\n\n"
                "Then send a voice note saying exactly that transcript.\n"
                "Custom voices work for both single TTS and duo host."
            )}

        add_parts = subargs.split(None, 1)
        voice_name = add_parts[0]
        ref_text = add_parts[1] if len(add_parts) > 1 else ""

        set_pending_voice(user_jid, voice_name, ref_text)

        msg = f"Ready to add voice *{voice_name}*.\n\nNow send a voice note"
        if ref_text:
            msg += f" saying:\n\"{ref_text}\""
        else:
            msg += f". For best results, include a transcript:\n/voice add {voice_name} <what you'll say>"
        return {"reply_text": msg}

    # --- REMOVE ---
    if subcmd == "remove":
        if len(parts) < 2:
            return {"reply_text": "Usage: /voice remove <name|c#>"}
        input_str = parts[1]
        # Resolve c# to name
        m = _CUSTOM_NUM_RE.match(input_str)
        if m:
            name = custom_voice_by_number(user_jid, int(m.group(1)))
            if not name:
                return {"reply_text": f"Custom voice '{input_str}' not found."}
        else:
            name = input_str
        if remove_custom_voice(user_jid, name):
            return {"reply_text": f"Voice *{name}* removed."}
        return {"reply_text": f"Custom voice '{input_str}' not found."}

    return {"reply_text": "Unknown subcommand. Use /voice for help."}


def _set_single(user_jid: str, rest: list[str]) -> dict:
    """Set single TTS voice (Kokoro or custom)."""
    if not rest:
        return {"reply_text": "Usage: /voice set single <#, c#, or name>"}
    input_str = " ".join(rest)
    name = _resolve_any_voice(input_str, user_jid, "kokoro")
    if name and set_active_voice(user_jid, name):
        return {"reply_text": f"Single voice set to *{_display_active(name)}*."}
    return {"reply_text": f"Voice '{input_str}' not found. Use /voice list to see options."}


def _set_duo(user_jid: str, rest: list[str]) -> dict:
    """Set duo dialogue voices."""
    if not rest:
        return {"reply_text": (
            "Usage:\n"
            "/voice set duo <host#> <guest#>\n"
            "/voice set duo host <#|c#|name>\n"
            "/voice set duo guest <#|name>"
        )}

    # /voice set duo host <value>
    if rest[0].lower() == "host":
        if len(rest) < 2:
            return {"reply_text": "Usage: /voice set duo host <#, c#, or name>"}
        return _set_duo_role(user_jid, "host", " ".join(rest[1:]))

    # /voice set duo guest <value>
    if rest[0].lower() == "guest":
        if len(rest) < 2:
            return {"reply_text": "Usage: /voice set duo guest <# or name>"}
        return _set_duo_role(user_jid, "guest", " ".join(rest[1:]))

    # /voice set duo <host> <guest>
    if len(rest) >= 2:
        host_result = _set_duo_role(user_jid, "host", rest[0])
        guest_result = _set_duo_role(user_jid, "guest", rest[1])
        h_ok = "set to" in host_result["reply_text"]
        g_ok = "set to" in guest_result["reply_text"]
        if h_ok and g_ok:
            voices = list_voices(user_jid)
            return {"reply_text": (
                f"Duo set — host: *{_display_active(voices['dialogue_host'])}*, "
                f"guest: *{_display_active(voices['dialogue_guest'])}*."
            )}
        if not h_ok:
            return host_result
        return guest_result

    # Single arg: set host only
    return _set_duo_role(user_jid, "host", rest[0])


def _set_duo_role(user_jid: str, role: str, input_str: str) -> dict:
    """Set duo host or guest voice."""
    if role == "guest":
        # Guest must be a VibeVoice preset (no custom)
        name = _resolve_any_voice(input_str, user_jid, "vibevoice")
        vv_names = get_voice_names("vibevoice")
        if name and name in vv_names and set_dialogue_guest(user_jid, name):
            return {"reply_text": f"Duo guest set to *{_display_active(name)}*."}
        return {"reply_text": f"VibeVoice '{input_str}' not found. Guest must be a VibeVoice preset."}

    # Host: vibevoice preset or custom
    name = _resolve_any_voice(input_str, user_jid, "vibevoice")
    if name and set_dialogue_host(user_jid, name):
        vv_names = get_voice_names("vibevoice")
        suffix = " (custom, voice cloned)" if name not in vv_names else ""
        return {"reply_text": f"Duo host set to *{_display_active(name)}*{suffix}."}
    return {"reply_text": f"Voice '{input_str}' not found. Use /voice list for options."}
