"""Per-user Google OAuth token storage with file locking."""
from __future__ import annotations
import json
import os
import fcntl
import logging
from agent.jid import jid_to_number

log = logging.getLogger(__name__)

GOOGLE_DIR = os.getenv("GOOGLE_AUTH_DIR", "data/google")


def _token_path(user_jid: str) -> str:
    os.makedirs(GOOGLE_DIR, exist_ok=True)
    return os.path.join(GOOGLE_DIR, f"{jid_to_number(user_jid)}.json")


def load_google_tokens(user_jid: str) -> dict:
    path = _token_path(user_jid)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def save_google_tokens(user_jid: str, data: dict) -> None:
    path = _token_path(user_jid)
    os.makedirs(GOOGLE_DIR, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    log.debug("Saved Google tokens for %s", jid_to_number(user_jid))


def clear_google_tokens(user_jid: str) -> None:
    path = _token_path(user_jid)
    if os.path.exists(path):
        os.remove(path)
        log.info("Cleared Google tokens for %s", jid_to_number(user_jid))


def is_linked(user_jid: str) -> bool:
    tokens = load_google_tokens(user_jid)
    return bool(tokens.get("refresh_token"))


def set_pending_link(user_jid: str) -> None:
    tokens = load_google_tokens(user_jid)
    tokens["pending_link"] = True
    save_google_tokens(user_jid, tokens)


def has_pending_link(user_jid: str) -> bool:
    tokens = load_google_tokens(user_jid)
    return bool(tokens.get("pending_link"))


def clear_pending_link(user_jid: str) -> None:
    tokens = load_google_tokens(user_jid)
    tokens.pop("pending_link", None)
    save_google_tokens(user_jid, tokens)
