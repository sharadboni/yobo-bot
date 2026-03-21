"""User profile model."""
from __future__ import annotations
import time


def new_profile(jid: str, push_name: str = "") -> dict:
    return {
        "jid": jid,
        "push_name": push_name,
        "approved": False,
        "ignored": False,
        "created_at": time.time(),
        "last_seen": time.time(),
        "history": [],
    }
