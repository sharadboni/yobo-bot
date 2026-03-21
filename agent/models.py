"""User profile model."""
from __future__ import annotations
import time
from typing import TypedDict


class HistoryEntry(TypedDict):
    role: str        # "user" or "assistant"
    content: str
    ts: float


class UserProfile(TypedDict):
    jid: str
    push_name: str
    approved: bool
    ignored: bool
    created_at: float
    last_seen: float
    history: list[HistoryEntry]


def new_profile(jid: str, push_name: str = "") -> UserProfile:
    return {
        "jid": jid,
        "push_name": push_name,
        "approved": False,
        "ignored": False,
        "created_at": time.time(),
        "last_seen": time.time(),
        "history": [],
    }
