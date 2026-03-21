"""Load or create user profile. Handle admin approval flow."""
from __future__ import annotations
import logging
from agent.services.user_store import load_user as _load, save_user as _save
from agent.jid import jid_to_number

log = logging.getLogger(__name__)


def load_user_node(state: dict) -> dict:
    jid = state["user_jid"]
    push_name = state.get("push_name", "")
    number = jid_to_number(jid)

    profile = _load(jid, push_name)
    is_new = profile.get("created_at") == profile.get("last_seen")

    # New user: save profile (unapproved) and notify admin
    if is_new:
        _save(profile)
        display = f"{push_name} ({number})" if push_name else number
        return {
            "user_profile": profile,
            "reply_text": "Your request has been sent to the admin. Please wait for approval.",
            "outbound": [
                {
                    "type": "admin_notify",
                    "content": {
                        "text": (
                            f"New contact: {display}\n"
                            f"Reply:  /add {number}  or  /ignore {number}"
                        ),
                    },
                }
            ],
            "intent": "__pending__",
        }

    # Ignored user
    if profile.get("ignored"):
        return {
            "user_profile": profile,
            "reply_text": "",
            "outbound": [],
            "intent": "__ignored__",
        }

    # Unapproved user (waiting for admin)
    if not profile.get("approved"):
        return {
            "user_profile": profile,
            "reply_text": "Still waiting for admin approval.",
            "outbound": [],
            "intent": "__pending__",
        }

    return {"user_profile": profile}
