"""Save user profile with updated history."""
from __future__ import annotations
import time
import logging
from agent.services.user_store import save_user as _save

log = logging.getLogger(__name__)


def save_user_node(state: dict) -> dict:
    profile = state.get("user_profile")
    if not profile:
        return {}

    intent = state.get("intent", "")
    # Don't save history for pending/ignored
    if intent in ("__pending__", "__ignored__", "__error__"):
        return {}

    resolved = state.get("resolved_text", "")
    reply = state.get("reply_text", "")

    if resolved:
        profile.setdefault("history", []).append({
            "role": "user",
            "content": resolved,
            "ts": time.time(),
        })
    if reply:
        profile["history"].append({
            "role": "assistant",
            "content": reply,
            "ts": time.time(),
        })

    profile["last_seen"] = time.time()
    _save(profile)
    return {}
