"""Admin command handling — /add, /ignore, /clear."""
from __future__ import annotations
import logging
from agent.services.user_store import approve_user, ignore_user
from agent.jid import jid_to_number, is_group_jid

log = logging.getLogger(__name__)


class AdminState:
    """Tracks admin JID and last pending user/group."""

    def __init__(self):
        self.admin_jid: str = ""
        self.last_pending_id: str = ""  # full JID for groups, number for users

    def is_admin(self, sender: str) -> bool:
        if not self.admin_jid:
            return False
        return (
            sender == self.admin_jid
            or sender.split(":")[0].split("@")[0] == self.admin_jid.split(":")[0].split("@")[0]
        )

    def track_pending(self, jid: str) -> None:
        """Track the last user/group that triggered an admin notification."""
        # Groups: store full JID. Users: store just the number.
        self.last_pending_id = jid if is_group_jid(jid) else jid_to_number(jid)


async def handle_admin_command(send_fn, admin: AdminState, sender: str, text: str) -> bool:
    """Handle admin commands. Returns True if a command was handled."""
    cmd = text.strip().lower()

    if cmd == "/help":
        await send_fn({"type": "reply", "to": sender, "content": {"text": (
            "*Admin Commands:*\n\n"
            "/add <number> — Approve a user\n"
            "/add — Approve the last pending user\n"
            "/ignore <number> — Ignore a user\n"
            "/clear — Clear all WhatsApp chats\n"
            "/clear <number> — Clear a specific chat\n"
            "/help — Show this message\n\n"
            "*User Commands (also available to admin):*\n\n"
            "/search <query> — Search the web\n"
            "/podcast <topic> — Generate a podcast voice note\n"
            "/schedule <type> <freq> [day] <time> [--audio] <topic> — Schedule a task\n"
            "/schedules — List your scheduled tasks\n"
            "/unschedule <id> — Remove a scheduled task\n"
            "/voice — Manage TTS voice\n\n"
            "Or just send a message to chat!"
        )}})
        return True

    if cmd.startswith("/add"):
        arg = text.strip()[4:].strip().lstrip("+")
        target = arg if arg else admin.last_pending_id
        if not target:
            await send_fn({"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /add <number or group JID>"}})
            return True
        if approve_user(target):
            is_grp = is_group_jid(target) or "@g.us" in target
            label = f"Group {target}" if is_grp else f"User {target}"
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"{label} approved."}})
            # Notify the approved user/group
            notify_jid = target if is_grp else f"{target}@s.whatsapp.net"
            await send_fn({"type": "reply", "to": notify_jid, "content": {"text": (
                "This group has been approved! Send /help to see what I can do." if is_grp
                else "You've been approved! Send /help to see what I can do."
            )}})
            if target == admin.last_pending_id:
                admin.last_pending_id = ""
        else:
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"{target} not found."}})
        return True

    if cmd == "/clear" or cmd.startswith("/clear "):
        arg = text.strip()[6:].strip()
        await send_fn({"type": "clear_chats", "target": arg or "all"})
        await send_fn({"type": "reply", "to": sender, "content": {"text": f"Clearing chats: {arg or 'all'}..."}})
        return True

    if cmd.startswith("/ignore"):
        arg = text.strip()[7:].strip().lstrip("+")
        target = arg if arg else admin.last_pending_id
        if not target:
            await send_fn({"type": "reply", "to": sender, "content": {"text": "No pending user. Use: /ignore <number or group JID>"}})
            return True
        if ignore_user(target):
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"{target} will be ignored."}})
            if target == admin.last_pending_id:
                admin.last_pending_id = ""
        else:
            await send_fn({"type": "reply", "to": sender, "content": {"text": f"{target} not found."}})
        return True

    return False
