"""Voice sample storage for TTS voice cloning."""
from __future__ import annotations
import base64
import json
import os
import fcntl
import logging

import httpx

from agent.config import LLM_CONFIG

log = logging.getLogger(__name__)

VOICES_DIR = os.getenv("VOICES_DIR", "data/voices")

# Hardcoded fallbacks (used when server is unreachable)
_KOKORO_FALLBACK = [
    "af_heart", "af_bella", "af_nova", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
    "bf_alice", "bf_emma", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
]
_VIBEVOICE_FALLBACK = ["en-Emma_woman", "en-Carter_man"]

# Cached voice data from server: model -> [{"name": ..., "language": ..., "gender": ...}]
_voice_cache: dict[str, list[dict]] = {}


def _tts_base_url() -> str:
    """Get the first TTS provider's base_url from llm_config.yaml."""
    providers = LLM_CONFIG.get("tts", [])
    if providers:
        return providers[0]["base_url"].rstrip("/")
    return "http://10.0.0.3:8765/v1"


async def refresh_voices() -> None:
    """Fetch voice lists from the mlx-omni-server and cache them."""
    base = _tts_base_url()
    for model in ("kokoro", "vibevoice"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base}/audio/voices", params={"model": model})
                resp.raise_for_status()
                voices = resp.json().get("voices", [])
                if voices:
                    _voice_cache[model] = voices
                    log.info("Fetched %d %s voices from server", len(voices), model)
        except Exception as e:
            log.warning("Failed to fetch %s voices: %s", model, e)


def get_voice_names(model: str = "kokoro") -> list[str]:
    """Return voice name list for a model (cached or fallback)."""
    if model in _voice_cache:
        return [v["name"] for v in _voice_cache[model]]
    return list(_KOKORO_FALLBACK) if model == "kokoro" else list(_VIBEVOICE_FALLBACK)


def get_voice_metadata(model: str = "kokoro") -> list[dict]:
    """Return full voice metadata list (cached or empty)."""
    return _voice_cache.get(model, [])


# Backward compat alias
BUILTIN_VOICES = _KOKORO_FALLBACK


def _user_voice_dir(user_jid: str) -> str:
    from agent.jid import jid_to_number
    number = jid_to_number(user_jid)
    path = os.path.join(VOICES_DIR, number)
    os.makedirs(path, exist_ok=True)
    return path


def _voice_meta_path(user_jid: str) -> str:
    return os.path.join(_user_voice_dir(user_jid), "voices.json")


def _load_meta(user_jid: str) -> dict:
    path = _voice_meta_path(user_jid)
    if not os.path.exists(path):
        return {
            "active": "af_heart",
            "dialogue_host": "en-Emma_woman",
            "dialogue_guest": "en-Carter_man",
            "custom": {},
            "pending": None,
        }
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    # Migration from old single dialogue_voice field
    if "dialogue_host" not in data:
        old = data.pop("dialogue_voice", "en-Emma_woman")
        data["dialogue_host"] = old
    if "dialogue_guest" not in data:
        data["dialogue_guest"] = "en-Carter_man"
    return data


def _save_meta(user_jid: str, meta: dict) -> None:
    path = _voice_meta_path(user_jid)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(meta, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def get_active_voice(user_jid: str) -> dict:
    """Get the user's active voice config (Kokoro, for regular TTS).
    Returns: {"name": str, "ref_audio_b64": str|None, "ref_text": str|None}
    """
    meta = _load_meta(user_jid)
    active = meta.get("active", "af_heart")
    return _load_voice_config(user_jid, active)


def _load_voice_config(user_jid: str, voice_name: str) -> dict:
    """Build a voice config dict for any voice (builtin or custom).
    Returns: {"name": str, "ref_audio_b64": str|None, "ref_text": str|None}
    """
    # Check builtins first (kokoro + vibevoice)
    all_builtin = set(get_voice_names("kokoro") + get_voice_names("vibevoice"))
    if voice_name in all_builtin:
        return {"name": voice_name, "ref_audio_b64": None, "ref_text": None}

    # Custom voice
    meta = _load_meta(user_jid)
    custom = meta.get("custom", {}).get(voice_name)
    if not custom:
        return {"name": voice_name, "ref_audio_b64": None, "ref_text": None}

    audio_path = os.path.join(_user_voice_dir(user_jid), custom["filename"])
    if not os.path.exists(audio_path):
        log.warning("Voice audio missing: %s", audio_path)
        return {"name": voice_name, "ref_audio_b64": None, "ref_text": None}

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    return {"name": voice_name, "ref_audio_b64": audio_b64, "ref_text": custom.get("ref_text")}


def get_dialogue_voices(user_jid: str) -> dict:
    """Get the user's dialogue host and guest voice configs.
    Returns: {"host": {"name", "ref_audio_b64", "ref_text"}, "guest": {"name", ...}}
    Host can be a custom voice (with ref_audio for cloning) or a VibeVoice preset.
    Guest is always a VibeVoice preset.
    """
    meta = _load_meta(user_jid)
    host_name = meta.get("dialogue_host", "en-Emma_woman")
    guest_name = meta.get("dialogue_guest", "en-Carter_man")

    host = _load_voice_config(user_jid, host_name)
    guest = {"name": guest_name, "ref_audio_b64": None, "ref_text": None}
    return {"host": host, "guest": guest}


def set_dialogue_host(user_jid: str, voice_name: str) -> bool:
    """Set dialogue host voice. Can be a VibeVoice preset or a custom voice."""
    vv_names = get_voice_names("vibevoice")
    meta = _load_meta(user_jid)
    custom_names = list(meta.get("custom", {}).keys())
    if voice_name not in vv_names and voice_name not in custom_names:
        return False
    meta["dialogue_host"] = voice_name
    _save_meta(user_jid, meta)
    return True


def set_dialogue_guest(user_jid: str, voice_name: str) -> bool:
    """Set dialogue guest voice. Must be a VibeVoice preset."""
    vv_names = get_voice_names("vibevoice")
    if voice_name not in vv_names:
        return False
    meta = _load_meta(user_jid)
    meta["dialogue_guest"] = voice_name
    _save_meta(user_jid, meta)
    return True


def set_active_voice(user_jid: str, voice_name: str) -> bool:
    """Set the user's active voice (Kokoro). Returns True if voice exists."""
    meta = _load_meta(user_jid)
    kokoro_names = get_voice_names("kokoro")
    if voice_name in kokoro_names:
        meta["active"] = voice_name
        _save_meta(user_jid, meta)
        return True
    clean = _sanitize_name(voice_name)
    if clean in meta.get("custom", {}):
        meta["active"] = clean
        _save_meta(user_jid, meta)
        return True
    return False


def _sanitize_name(name: str) -> str:
    """Sanitize voice name — alphanumeric, dashes, underscores only."""
    import re
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    return clean[:32] or "voice"


def _convert_to_wav(audio_bytes: bytes) -> bytes:
    """Convert any audio format to WAV using ffmpeg."""
    import subprocess
    import tempfile
    in_file = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    out_file = in_file.name.replace(".ogg", ".wav")
    try:
        in_file.write(audio_bytes)
        in_file.close()
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", in_file.name, "-ar", "16000", "-ac", "1", out_file],
            capture_output=True,
        )
        if result.returncode != 0:
            log.warning("ffmpeg conversion failed, saving raw: %s", result.stderr.decode()[:200])
            return audio_bytes
        with open(out_file, "rb") as f:
            return f.read()
    finally:
        os.unlink(in_file.name)
        if os.path.exists(out_file):
            os.unlink(out_file)


def add_custom_voice(user_jid: str, name: str, audio_bytes: bytes, ref_text: str = "") -> bool:
    """Store a custom voice sample. Converts to WAV for compatibility."""
    name = _sanitize_name(name)
    meta = _load_meta(user_jid)
    voice_dir = _user_voice_dir(user_jid)

    # Convert to WAV (WhatsApp sends ogg/opus)
    wav_bytes = _convert_to_wav(audio_bytes)

    # Save audio file
    filename = f"{name}.wav"
    audio_path = os.path.join(voice_dir, filename)
    with open(audio_path, "wb") as f:
        f.write(wav_bytes)

    # Update metadata
    custom = meta.get("custom", {})
    custom[name] = {"filename": filename, "ref_text": ref_text}
    meta["custom"] = custom
    _save_meta(user_jid, meta)
    log.info("Custom voice added: %s for %s", name, user_jid)
    return True


def remove_custom_voice(user_jid: str, name: str) -> bool:
    """Remove a custom voice. Returns True if found."""
    name = _sanitize_name(name)
    meta = _load_meta(user_jid)
    custom = meta.get("custom", {})
    if name not in custom:
        return False

    # Delete audio file
    voice_dir = _user_voice_dir(user_jid)
    filename = custom[name]["filename"]
    audio_path = os.path.join(voice_dir, filename)
    if os.path.exists(audio_path):
        os.unlink(audio_path)

    del custom[name]
    meta["custom"] = custom
    if meta.get("active") == name:
        meta["active"] = "af_heart"
    _save_meta(user_jid, meta)
    return True


def list_voices(user_jid: str) -> dict:
    """List all available voices for a user.
    Returns: {"active": str, "dialogue_host": str, "dialogue_guest": str,
              "kokoro": list[dict], "vibevoice": list[dict], "custom": list[str]}
    """
    meta = _load_meta(user_jid)
    kokoro = get_voice_metadata("kokoro") or [{"name": n} for n in _KOKORO_FALLBACK]
    vibevoice = get_voice_metadata("vibevoice") or [{"name": n} for n in _VIBEVOICE_FALLBACK]
    return {
        "active": meta.get("active", "af_heart"),
        "dialogue_host": meta.get("dialogue_host", "en-Emma_woman"),
        "dialogue_guest": meta.get("dialogue_guest", "en-Carter_man"),
        "kokoro": kokoro,
        "vibevoice": vibevoice,
        "custom": list(meta.get("custom", {}).keys()),
    }


def voice_by_number(model: str, number: int) -> str | None:
    """Resolve a 1-based number to a voice name. Returns None if out of range."""
    names = get_voice_names(model)
    if 1 <= number <= len(names):
        return names[number - 1]
    return None


def custom_voice_by_number(user_jid: str, number: int) -> str | None:
    """Resolve a 1-based custom voice number (c1, c2, ...) to a name."""
    meta = _load_meta(user_jid)
    custom_names = sorted(meta.get("custom", {}).keys())
    if 1 <= number <= len(custom_names):
        return custom_names[number - 1]
    return None


def set_pending_voice(user_jid: str, name: str, ref_text: str = "") -> None:
    """Set a pending voice — waiting for the user to send audio."""
    meta = _load_meta(user_jid)
    meta["pending"] = {"name": name, "ref_text": ref_text}
    _save_meta(user_jid, meta)


def get_pending_voice(user_jid: str) -> dict | None:
    """Get the pending voice if any. Returns {"name": str, "ref_text": str} or None."""
    meta = _load_meta(user_jid)
    return meta.get("pending")


def clear_pending_voice(user_jid: str) -> None:
    """Clear the pending voice."""
    meta = _load_meta(user_jid)
    meta["pending"] = None
    _save_meta(user_jid, meta)
