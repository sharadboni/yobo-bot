"""Google account linking, calendar, email, tasks, and contacts skill."""
from __future__ import annotations
import logging
from datetime import date, timedelta
from agent.services.google_store import (
    is_linked, set_pending_link, clear_pending_link,
    clear_google_tokens, save_google_tokens,
)
from agent.services.google_api import (
    get_auth_url, exchange_code,
    get_calendar_events, format_events,
    get_unread_emails, get_email_body, send_email, format_emails,
    get_tasks, add_task, complete_task, format_tasks,
    search_contacts, format_contacts,
)

log = logging.getLogger(__name__)

_NOT_LINKED = "No Google account linked. Use /google link first."


def _get_sender_jid(state: dict) -> str:
    """Get the actual person's JID (sender in groups, user in DMs)."""
    return state.get("sender_jid") or state.get("user_jid", "")


async def google_cmd(state: dict) -> dict:
    """Handle /google command."""
    args = state.get("intent_args", "").strip()
    sender = _get_sender_jid(state)

    if not args:
        linked = is_linked(sender)
        if linked:
            return {"reply_text": (
                "Google account is linked.\n\n"
                "/google calendar — today's events\n"
                "/google calendar tomorrow\n"
                "/google emails — unread inbox\n"
                "/google email read 1 — read full email\n"
                "/google email send <to> <subject> | <body>\n"
                "/google tasks — pending tasks\n"
                "/google task add <title>\n"
                "/google task done <number>\n"
                "/google contacts <name> — search contacts\n"
                "/google unlink — disconnect account"
            )}
        return {"reply_text": (
            "Link your Google account to access calendar, email, tasks, and contacts.\n\n"
            "/google link — get authorization link"
        )}

    parts = args.split(None, 1)
    subcmd = parts[0].lower()
    sub_args = parts[1] if len(parts) > 1 else ""

    # ── Link / Unlink ────────────────────────────────────────────
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

    # ── Calendar ─────────────────────────────────────────────────
    if subcmd == "calendar":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}

        today = date.today()
        if sub_args.lower() == "tomorrow":
            target = today + timedelta(days=1)
            label = "tomorrow"
        elif sub_args:
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

    # ── Gmail ────────────────────────────────────────────────────
    if subcmd == "emails":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}
        count = 10
        if sub_args.isdigit():
            count = min(int(sub_args), 20)
        emails = await get_unread_emails(sender, count)
        return {"reply_text": format_emails(emails)}

    if subcmd == "email":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}

        email_parts = sub_args.split(None, 1)
        if not email_parts:
            return {"reply_text": "Usage: /google email read <number> or /google email send <to> <subject> | <body>"}

        action = email_parts[0].lower()
        email_args = email_parts[1] if len(email_parts) > 1 else ""

        if action == "read":
            # Read full email by index number
            idx = int(email_args.strip()) if email_args.strip().isdigit() else 0
            if idx < 1:
                return {"reply_text": "Usage: /google email read <number> (from /google emails list)"}
            emails = await get_unread_emails(sender, idx)
            if isinstance(emails, str):
                return {"reply_text": emails}
            if idx > len(emails):
                return {"reply_text": f"Only {len(emails)} unread emails."}
            email = emails[idx - 1]
            body = await get_email_body(sender, email["id"])
            # Truncate long emails
            if len(body) > 2000:
                body = body[:2000] + "\n\n[Truncated]"
            return {"reply_text": f"From: {email['from']}\nSubject: {email['subject']}\n\n{body}"}

        if action == "send":
            # Format: /google email send to@email.com Subject here | Body here
            if "|" in email_args:
                header, body = email_args.split("|", 1)
                header_parts = header.strip().split(None, 1)
                to = header_parts[0] if header_parts else ""
                subject = header_parts[1].strip() if len(header_parts) > 1 else "(no subject)"
                body = body.strip()
            else:
                parts = email_args.strip().split(None, 1)
                to = parts[0] if parts else ""
                subject = parts[1] if len(parts) > 1 else "(no subject)"
                body = ""

            if not to or "@" not in to:
                return {"reply_text": "Usage: /google email send <to@email.com> <subject> | <body>"}

            result = await send_email(sender, to, subject, body)
            return {"reply_text": result}

        return {"reply_text": "Usage: /google email read <number> or /google email send <to> <subject> | <body>"}

    # ── Tasks ────────────────────────────────────────────────────
    if subcmd == "tasks":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}
        tasks = await get_tasks(sender)
        return {"reply_text": format_tasks(tasks)}

    if subcmd == "task":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}

        task_parts = sub_args.split(None, 1)
        if not task_parts:
            return {"reply_text": "Usage: /google task add <title> or /google task done <number>"}

        action = task_parts[0].lower()
        task_args = task_parts[1] if len(task_parts) > 1 else ""

        if action == "add":
            if not task_args:
                return {"reply_text": "Usage: /google task add <title>"}
            result = await add_task(sender, task_args.strip())
            return {"reply_text": result}

        if action == "done":
            idx = int(task_args.strip()) if task_args.strip().isdigit() else 0
            if idx < 1:
                return {"reply_text": "Usage: /google task done <number> (from /google tasks list)"}
            tasks = await get_tasks(sender)
            if isinstance(tasks, str):
                return {"reply_text": tasks}
            if idx > len(tasks):
                return {"reply_text": f"Only {len(tasks)} tasks."}
            result = await complete_task(sender, tasks[idx - 1]["id"])
            return {"reply_text": result}

        return {"reply_text": "Usage: /google task add <title> or /google task done <number>"}

    # ── Contacts ─────────────────────────────────────────────────
    if subcmd == "contacts":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}
        if not sub_args:
            return {"reply_text": "Usage: /google contacts <name or email>"}
        contacts = await search_contacts(sender, sub_args.strip())
        return {"reply_text": format_contacts(contacts)}

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
            "Try /google calendar, /google emails, or /google tasks."
        )}
    except Exception as e:
        log.error("OAuth exchange failed for %s: %s", sender, e)
        return {"reply_text": "Authorization failed. Please try /google link again."}
