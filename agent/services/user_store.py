"""File-based user profile storage with locking."""
from __future__ import annotations
import json
import os
import fcntl
import time
import logging
from agent.config import DATA_DIR, MAX_HISTORY
from agent.models import new_profile
from agent.jid import jid_to_number, normalize_jid, number_to_jid

log = logging.getLogger(__name__)


def _user_path(jid: str) -> str:
    return os.path.join(DATA_DIR, f"{jid_to_number(jid)}.json")


def load_user(jid: str, push_name: str = "") -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _user_path(jid)
    if os.path.exists(path):
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            profile = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        profile["last_seen"] = time.time()
        if push_name and push_name != profile.get("push_name"):
            profile["push_name"] = push_name
        return profile
    return new_profile(normalize_jid(jid), push_name)


def save_user(profile: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _user_path(profile["jid"])
    # Trim history
    if len(profile.get("history", [])) > MAX_HISTORY:
        profile["history"] = profile["history"][-MAX_HISTORY:]
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(profile, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    log.debug("Saved profile %s", profile["jid"])


def approve_user(number: str) -> bool:
    """Mark a user as approved. Creates profile if it doesn't exist."""
    jid = number_to_jid(number) if "@" not in number else normalize_jid(number)
    profile = load_user(jid)
    profile["approved"] = True
    profile["ignored"] = False
    save_user(profile)
    return True


def ignore_user(number: str) -> bool:
    """Mark a user as ignored. Accepts number or JID. Returns True if found."""
    jid = number_to_jid(number) if "@" not in number else number
    path = _user_path(jid)
    if not os.path.exists(path):
        return False
    profile = load_user(jid)
    profile["ignored"] = True
    save_user(profile)
    return True
