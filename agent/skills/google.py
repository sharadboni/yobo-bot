"""Google account linking and calendar skill."""
from __future__ import annotations
import logging
from datetime import date, timedelta
from agent.services.google_store import (
    is_linked, set_pending_link, clear_pending_link,
    clear_google_tokens, save_google_tokens,
)
from agent.services.google_api import (
    get_auth_url, exchange_code, get_calendar_events, format_events,
)

log = logging.getLogger(__name__)


def _get_sender_jid(state: dict) -> str:
    """Get the actual person's JID (sender in groups, user in DMs)."""
    return state.get("sender_jid") or state.get("user_jid", "")


async def google_cmd(state: dict) -> dict:
    """Handle /google command."""
    args = state.get("intent_args", "").strip()
    sender = _get_sender_jid(state)
    is_group = state.get("is_group", False)

    if not args:
        linked = is_linked(sender)
        if linked:
            return {"reply_text": (
                "Google account is linked.\n\n"
                "/google calendar — today's events\n"
                "/google calendar tomorrow — tomorrow's events\n"
                "/google unlink — disconnect account"
            )}
        return {"reply_text": (
            "Link your Google account to access your calendar.\n\n"
            "/google link — get authorization link\n"
        )}

    parts = args.split(None, 1)
    subcmd = parts[0].lower()
    sub_args = parts[1] if len(parts) > 1 else ""

    if subcmd == "link":
        from agent.config import GOOGLE_CLIENT_ID
        if not GOOGLE_CLIENT_ID:
            return {"reply_text": "Google OAuth is not configured. Ask the admin to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."}
        set_pending_link(sender)
        url = get_auth_url()
        return {"reply_text": (
            f"Open this link to authorize:\n{url}\n\n"
            "After signing in, you'll see a code. Send it back to me here."
        )}

    if subcmd == "unlink":
        clear_google_tokens(sender)
        return {"reply_text": "Google account unlinked."}

    if subcmd == "calendar":
        if not is_linked(sender):
            return {"reply_text": "No Google account linked. Use /google link first."}

        if is_group:
            log.info("Calendar request in group from %s", sender)

        today = date.today()
        if sub_args.lower() == "tomorrow":
            target = today + timedelta(days=1)
            label = "tomorrow"
        elif sub_args:
            # Try to parse a date
            try:
                target = date.fromisoformat(sub_args.strip())
                label = target.isoformat()
            except ValueError:
                target = today
                label = "today"
        else:
            target = today
            label = "today"

        events = await get_calendar_events(sender, target.isoformat())
        return {"reply_text": format_events(events, label)}

    return {"reply_text": "Unknown subcommand. Use /google for help."}


async def google_link_callback(state: dict) -> dict:
    """Handle pasted OAuth authorization code."""
    code = state.get("intent_args", "").strip()
    sender = _get_sender_jid(state)

    if not code:
        return {"reply_text": "No authorization code provided."}

    try:
        token_data = await exchange_code(code)
        clear_pending_link(sender)
        save_google_tokens(sender, token_data)
        log.info("Google account linked for %s", sender)
        return {"reply_text": (
            "Google account linked!\n\n"
            "Try /google calendar to see today's events."
        )}
    except Exception as e:
        log.error("OAuth exchange failed for %s: %s", sender, e)
        return {"reply_text": f"Authorization failed. Please try /google link again."}
