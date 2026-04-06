"""Load or create user profile. Handle admin approval flow."""
from __future__ import annotations
import logging
from agent.services.user_store import load_user as _load, save_user as _save
from agent.jid import jid_to_number, is_group_jid

log = logging.getLogger(__name__)


def load_user_node(state: dict) -> dict:
    jid = state["user_jid"]
    push_name = state.get("push_name", "")
    is_group = state.get("is_group", False)

    profile = _load(jid, push_name)
    is_new = profile.get("created_at") == profile.get("last_seen")

    # New user/group: save profile (unapproved) and notify admin
    if is_new:
        _save(profile)
        if is_group:
            sender_name = state.get("sender_name", "")
            group_name = push_name or jid
            display = f"Group: {group_name}"
            if sender_name:
                display += f" (via {sender_name})"
            add_id = jid  # use full group JID for /add
        else:
            number = jid_to_number(jid)
            display = f"{push_name} ({number})" if push_name else number
            add_id = number
        return {
            "user_profile": profile,
            "reply_text": "" if is_group else "Your request has been sent to the admin. Please wait for approval.",
            "outbound": [
                {
                    "type": "admin_notify",
                    "content": {
                        "text": (
                            f"New {'group' if is_group else 'contact'}: {display}\n"
                            f"Reply:  /add {add_id}  or  /ignore {add_id}"
                        ),
                    },
                }
            ],
            "intent": "__pending__",
        }

    # Ignored
    if profile.get("ignored"):
        return {
            "user_profile": profile,
            "reply_text": "",
            "outbound": [],
            "intent": "__ignored__",
        }

    # Unapproved (waiting for admin)
    if not profile.get("approved"):
        return {
            "user_profile": profile,
            "reply_text": "" if is_group else "Still waiting for admin approval.",
            "outbound": [],
            "intent": "__pending__",
        }

    return {"user_profile": profile}
