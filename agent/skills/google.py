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
    get_calendar_events, create_calendar_event, update_calendar_event,
    delete_calendar_event, format_events,
    get_unread_emails, get_email_body, send_email, format_emails,
    get_tasks, add_task, complete_task, format_tasks,
    search_contacts, format_contacts,
    search_drive, list_recent_drive, read_drive_file, format_drive_files,
    list_keep_notes, create_keep_note, get_keep_note, delete_keep_note, format_keep_notes,
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
                "/google drive <query> — search Drive files\n"
                "/google drive recent — recent files\n"
                "/google drive read 1 — read file content\n"
                "/google unlink — disconnect account"
            )}
        return {"reply_text": (
            "Link your Google account to access calendar, email, tasks, contacts, and Drive.\n\n"
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

        # Sub-subcommands: add, edit, delete
        cal_parts = sub_args.split(None, 1) if sub_args else []
        cal_action = cal_parts[0].lower() if cal_parts else ""
        cal_args = cal_parts[1] if len(cal_parts) > 1 else ""

        if cal_action == "add":
            return await _calendar_add(sender, cal_args)

        if cal_action in ("edit", "update"):
            return await _calendar_edit(sender, cal_args)

        if cal_action in ("delete", "remove", "cancel"):
            return await _calendar_delete(sender, cal_args)

        # Default: list events
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

    # ── Drive ─────────────────────────────────────────────────────
    if subcmd == "drive":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}

        drive_parts = sub_args.split(None, 1) if sub_args else []
        drive_action = drive_parts[0].lower() if drive_parts else ""
        drive_args = drive_parts[1] if len(drive_parts) > 1 else ""

        if drive_action == "read":
            # Read by number from last search/list, or by search query
            if drive_args.strip().isdigit():
                idx = int(drive_args.strip())
                # Get recent files to find by index
                files = await list_recent_drive(sender)
                if isinstance(files, str):
                    return {"reply_text": files}
                if idx < 1 or idx > len(files):
                    return {"reply_text": f"Invalid file number. Use /google drive to list files."}
                f = files[idx - 1]
                content = await read_drive_file(sender, f["id"], f.get("mimeType", ""), f.get("name", ""))
                if len(content) > 3000:
                    content = content[:3000] + "\n\n[Truncated]"
                return {"reply_text": content}
            elif drive_args:
                # Search and read first match
                files = await search_drive(sender, drive_args.strip())
                if isinstance(files, str):
                    return {"reply_text": files}
                if not files:
                    return {"reply_text": f"No files found matching '{drive_args}'."}
                f = files[0]
                content = await read_drive_file(sender, f["id"], f.get("mimeType", ""), f.get("name", ""))
                if len(content) > 3000:
                    content = content[:3000] + "\n\n[Truncated]"
                return {"reply_text": content}
            else:
                return {"reply_text": "Usage: /google drive read <number> or /google drive read <search query>"}

        if not sub_args or drive_action == "recent":
            files = await list_recent_drive(sender)
        else:
            files = await search_drive(sender, sub_args.strip())
        return {"reply_text": format_drive_files(files)}

    # ── Keep Notes ───────────────────────────────────────────────
    if subcmd == "notes":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}
        notes = await list_keep_notes(sender)
        return {"reply_text": format_keep_notes(notes)}

    if subcmd == "note":
        if not is_linked(sender):
            return {"reply_text": _NOT_LINKED}

        note_parts = sub_args.split(None, 1)
        if not note_parts:
            return {"reply_text": "Usage: /google note add <title> | <body>, /google note read <number>, /google note delete <number>"}

        action = note_parts[0].lower()
        note_args = note_parts[1] if len(note_parts) > 1 else ""

        if action == "add":
            if not note_args:
                return {"reply_text": "Usage: /google note add <title> | <body>"}
            if "|" in note_args:
                title, body = note_args.split("|", 1)
                result = await create_keep_note(sender, title.strip(), body.strip())
            else:
                result = await create_keep_note(sender, note_args.strip())
            return {"reply_text": result}

        if action == "read":
            idx = int(note_args.strip()) if note_args.strip().isdigit() else 0
            if idx < 1:
                return {"reply_text": "Usage: /google note read <number> (from /google notes list)"}
            notes = await list_keep_notes(sender)
            if isinstance(notes, str):
                return {"reply_text": notes}
            if idx > len(notes):
                return {"reply_text": f"Only {len(notes)} notes."}
            content = await get_keep_note(sender, notes[idx - 1]["name"])
            return {"reply_text": content}

        if action in ("delete", "remove"):
            idx = int(note_args.strip()) if note_args.strip().isdigit() else 0
            if idx < 1:
                return {"reply_text": "Usage: /google note delete <number>"}
            notes = await list_keep_notes(sender)
            if isinstance(notes, str):
                return {"reply_text": notes}
            if idx > len(notes):
                return {"reply_text": f"Only {len(notes)} notes."}
            result = await delete_keep_note(sender, notes[idx - 1]["name"])
            return {"reply_text": result}

        return {"reply_text": "Usage: /google note add <title> | <body>, /google note read <number>, /google note delete <number>"}

    return {"reply_text": "Unknown subcommand. Use /google for help."}


# ── Calendar helpers ─────────────────────────────────────────────

import re

_TIME_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})"          # date
    r"(?:\s+(\d{1,2}(?::\d{2})?)"   # start time
    r"(?:\s*-\s*(\d{1,2}(?::\d{2})?))?"  # optional end time
    r")?"
)


def _parse_time(t: str, ref_date: str) -> str:
    """Normalize a time like '3pm', '15:00', '3:30' to ISO datetime."""
    t = t.strip().lower()
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return f"{ref_date}T00:00:00"
    h = int(m.group(1))
    mins = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    return f"{ref_date}T{h:02d}:{mins:02d}:00"


async def _calendar_add(sender: str, args: str) -> dict:
    """Parse and create a calendar event.
    Format: /google calendar add <title> on <date> [at <start>[-<end>]] [at <location>]
    Examples:
        /google calendar add Lunch with Bob on 2026-04-07 at 12pm-1pm
        /google calendar add Team standup on 2026-04-07 at 9:30am
        /google calendar add Day off on 2026-04-10
    """
    if not args:
        return {"reply_text": (
            "Usage: /google calendar add <title> on <date> [at <time>[-<end>]]\n\n"
            "Examples:\n"
            "  /google calendar add Lunch on 2026-04-07 at 12pm-1pm\n"
            "  /google calendar add Meeting on 2026-04-07 at 3pm\n"
            "  /google calendar add Day off on 2026-04-10"
        )}

    # Parse "on <date>"
    on_match = re.search(r"\bon\s+(\d{4}-\d{2}-\d{2})", args)
    if not on_match:
        return {"reply_text": "Please include a date: /google calendar add <title> on YYYY-MM-DD [at time]"}

    event_date = on_match.group(1)
    title = args[:on_match.start()].strip()
    rest = args[on_match.end():].strip()

    if not title:
        return {"reply_text": "Please include a title before 'on'."}

    # Parse "at <time>[-<end>]"
    location = ""
    at_match = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:-\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?", rest, re.I)
    if at_match:
        start_time = _parse_time(at_match.group(1), event_date)
        if at_match.group(2):
            end_time = _parse_time(at_match.group(2), event_date)
        else:
            # Default 1 hour duration
            from datetime import datetime, timedelta as td
            dt = datetime.fromisoformat(start_time)
            end_time = (dt + td(hours=1)).isoformat()
        # Check for location after time
        after_time = rest[at_match.end():].strip()
        loc_match = re.search(r"\bat\s+(.+)", after_time, re.I)
        if loc_match:
            location = loc_match.group(1).strip()
    else:
        # All-day event
        start_time = event_date
        end_time = event_date
        # Check for location
        loc_match = re.search(r"\bat\s+(.+)", rest, re.I)
        if loc_match:
            location = loc_match.group(1).strip()

    result = await create_calendar_event(sender, title, start_time, end_time, location)
    return {"reply_text": result}


async def _calendar_edit(sender: str, args: str) -> dict:
    """Edit a calendar event by number from today's list.
    Format: /google calendar edit <number> <field> <value>
    Fields: title, time, location
    """
    if not args:
        return {"reply_text": (
            "Usage: /google calendar edit <number> <field> <value>\n\n"
            "Fields: title, time <start>-<end>, location\n"
            "Number refers to the event list from /google calendar"
        )}

    parts = args.split(None, 2)
    if len(parts) < 2 or not parts[0].isdigit():
        return {"reply_text": "Usage: /google calendar edit <number> title|time|location <value>"}

    idx = int(parts[0])
    field = parts[1].lower()
    value = parts[2] if len(parts) > 2 else ""

    # Get today's events to find the event ID
    today = date.today()
    events = await get_calendar_events(sender, today.isoformat())
    if isinstance(events, str):
        return {"reply_text": events}
    if idx < 1 or idx > len(events):
        return {"reply_text": f"Invalid event number. You have {len(events)} events today."}

    event = events[idx - 1]
    event_id = event["id"]

    if field == "title" and value:
        result = await update_calendar_event(sender, event_id, summary=value)
    elif field == "time" and value:
        # Parse time range like "3pm-4pm"
        time_match = re.match(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*-\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", value, re.I)
        if time_match:
            start = _parse_time(time_match.group(1), today.isoformat())
            end = _parse_time(time_match.group(2), today.isoformat())
            result = await update_calendar_event(sender, event_id, start_time=start, end_time=end)
        else:
            start = _parse_time(value, today.isoformat())
            from datetime import datetime, timedelta as td
            dt = datetime.fromisoformat(start)
            end = (dt + td(hours=1)).isoformat()
            result = await update_calendar_event(sender, event_id, start_time=start, end_time=end)
    elif field == "location" and value:
        result = await update_calendar_event(sender, event_id, location=value)
    else:
        return {"reply_text": "Usage: /google calendar edit <number> title|time|location <value>"}

    return {"reply_text": result}


async def _calendar_delete(sender: str, args: str) -> dict:
    """Delete a calendar event by number from today's list."""
    if not args or not args.strip().isdigit():
        return {"reply_text": "Usage: /google calendar delete <number>\nNumber refers to the event list from /google calendar"}

    idx = int(args.strip())
    today = date.today()
    events = await get_calendar_events(sender, today.isoformat())
    if isinstance(events, str):
        return {"reply_text": events}
    if idx < 1 or idx > len(events):
        return {"reply_text": f"Invalid event number. You have {len(events)} events today."}

    event = events[idx - 1]
    result = await delete_calendar_event(sender, event["id"])
    return {"reply_text": result}


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
