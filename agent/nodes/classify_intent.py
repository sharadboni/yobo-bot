"""Classify intent: match slash commands to skills, default to chat."""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)

# Slash command → skill mapping
SKILL_MAP = {
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
    "/help": "help",
}


def classify_intent_node(state: dict) -> dict:
    # Skip if intent already set (pending/ignored/error)
    if state.get("intent") and state["intent"].startswith("__"):
        return {}

    text = state.get("resolved_text", "").strip()

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
