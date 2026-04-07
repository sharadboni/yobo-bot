"""Classify intent: match slash commands to skills, default to chat."""
from __future__ import annotations
import re
import logging

log = logging.getLogger(__name__)

# Slash command → skill mapping
SKILL_MAP = {
    "/news": "news",
    "/n": "news",
    "/search": "web_search",
    "/s": "web_search",
    "/podcast": "podcast",
    "/p": "podcast",
    "/schedule": "schedule_add",
    "/schedules": "schedule_list",
    "/unschedule": "schedule_remove",
    "/voice": "voice",
    "/v": "voice",
    "/say": "say",
    "/google": "google",
    "/g": "google",
    "/help": "help",
}

# Google OAuth authorization code pattern (e.g. 4/0AXxxx...)
_OAUTH_CODE_RE = re.compile(r"^4/0A[A-Za-z0-9_-]{20,}$")


def classify_intent_node(state: dict) -> dict:
    # Skip if intent already set (pending/ignored/error)
    if state.get("intent") and state["intent"].startswith("__"):
        return {}

    text = state.get("resolved_text", "").strip()

    # Check for Google OAuth code (only if user has a pending link)
    if _OAUTH_CODE_RE.match(text):
        from agent.services.google_store import has_pending_link
        sender = state.get("sender_jid") or state.get("user_jid", "")
        if has_pending_link(sender):
            return {"intent": "google_link_callback", "intent_args": text}

    # Check for slash commands
    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in SKILL_MAP:
            return {"intent": SKILL_MAP[cmd], "intent_args": args}

        # Unknown command → treat as chat
        return {"intent": "text_chat", "intent_args": ""}

    return {"intent": "text_chat", "intent_args": ""}
