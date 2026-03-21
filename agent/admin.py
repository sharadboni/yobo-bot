"""Admin command handling — /add, /ignore, /clear."""
from __future__ import annotations
import logging
from agent.services.user_store import approve_user, ignore_user
from agent.jid import jid_to_number

log = logging.getLogger(__name__)


class AdminState:
    """Tracks admin JID and last pending user number."""

    def __init__(self):
        self.admin_jid: str = ""
        self.last_pending_number: str = ""

    def is_admin(self, sender: str) -> bool:
        if not self.admin_jid:
            return False
        return (
            sender == self.admin_jid
            or sender.split(":")[0].split("@")[0] == self.admin_jid.split(":")[0].split("@")[0]
        )

    def track_pending(self, sender_jid: str) -> None:
        """Track the last user who triggered an admin notification."""
        self.last_pending_number = jid_to_number(sender_jid)


async def handle_admin_command(send_fn, admin: AdminState, sender: str, text: str) -> bool:
    """Handle admin commands. Returns True if a command was handled."""
    cmd = text.strip().lower()

    if cmd.startswith("/add"):
        arg = text.strip()[4:].strip().lstrip("+")
        number = arg if arg else admin.last_pending_number
        if not number:
            await send_fn({"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /add <number>"}})
            return True
        if approve_user(number):
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"User {number} approved."}})
            if number == admin.last_pending_number:
                admin.last_pending_number = ""
        else:
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"User {number} not found."}})
        return True

    if cmd == "/clear" or cmd.startswith("/clear "):
        arg = text.strip()[6:].strip()
        await send_fn({"type": "clear_chats", "target": arg or "all"})
        await send_fn({"type": "reply", "to": sender, "content": {"text": f"Clearing chats: {arg or 'all'}..."}})
        return True

    if cmd.startswith("/ignore"):
        arg = text.strip()[7:].strip().lstrip("+")
        number = arg if arg else admin.last_pending_number
        if not number:
            await send_fn({"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /ignore <number>"}})
            return True
        if ignore_user(number):
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"User {number} will be ignored."}})
            if number == admin.last_pending_number:
                admin.last_pending_number = ""
        else:
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"User {number} not found."}})
        return True

    return False
