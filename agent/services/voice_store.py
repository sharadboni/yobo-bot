"""Voice sample storage for TTS voice cloning."""
from __future__ import annotations
import base64
import json
import os
import fcntl
import logging

log = logging.getLogger(__name__)

VOICES_DIR = os.getenv("VOICES_DIR", "data/voices")

# Built-in Kokoro voice presets (no ref audio needed)
BUILTIN_VOICES = [
    # American English
    "af_heart", "af_bella", "af_nova", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_liam",
    # Spanish
    "ef_dora", "em_alex", "em_santa",
    # Hindi
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
]


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
        return {"active": "af_heart", "custom": {}, "pending": None}
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _save_meta(user_jid: str, meta: dict) -> None:
    path = _voice_meta_path(user_jid)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(meta, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def get_active_voice(user_jid: str) -> dict:
    """Get the user's active voice config.
    Returns: {"name": str, "ref_audio_b64": str|None, "ref_text": str|None}
    """
    meta = _load_meta(user_jid)
    active = meta.get("active", "af_heart")

    # Built-in voice
    if active in BUILTIN_VOICES:
        return {"name": active, "ref_audio_b64": None, "ref_text": None}

    # Custom voice
    custom = meta.get("custom", {}).get(active)
    if not custom:
        return {"name": "af_heart", "ref_audio_b64": None, "ref_text": None}

    # Load the audio file as base64
    audio_path = os.path.join(_user_voice_dir(user_jid), custom["filename"])
    if not os.path.exists(audio_path):
        log.warning("Voice audio missing: %s", audio_path)
        return {"name": "af_heart", "ref_audio_b64": None, "ref_text": None}

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    return {
        "name": active,
        "ref_audio_b64": audio_b64,
        "ref_text": custom.get("ref_text"),
    }


def set_active_voice(user_jid: str, voice_name: str) -> bool:
    """Set the user's active voice. Returns True if voice exists."""
    meta = _load_meta(user_jid)
    if voice_name in BUILTIN_VOICES:
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
    Returns: {"active": str, "builtin": list, "custom": list}
    """
    meta = _load_meta(user_jid)
    return {
        "active": meta.get("active", "af_heart"),
        "builtin": BUILTIN_VOICES,
        "custom": list(meta.get("custom", {}).keys()),
    }


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
