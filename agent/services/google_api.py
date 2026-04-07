"""Google OAuth2, Calendar, Gmail, Tasks, and Contacts APIs via httpx."""
from __future__ import annotations
import base64
import time
import logging
from email.mime.text import MIMEText
import httpx
from agent.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from agent.services.google_store import (
    load_google_tokens, save_google_tokens, clear_google_tokens,
)

log = logging.getLogger(__name__)

SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
])
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
PEOPLE_API = "https://people.googleapis.com/v1"
DRIVE_API = "https://www.googleapis.com/drive/v3"
TIMEOUT = 10
NOT_LINKED = "Google account not linked. Use /google link to connect."
EXPIRED = "Google access expired. Use /google link to reconnect."


def get_auth_url() -> str:
    """Generate a short URL that redirects to Google OAuth via the static page.
    WhatsApp strips underscores from URLs, so we let the static page build
    the actual Google auth URL with JavaScript (preserving response_type etc.)."""
    return f"{GOOGLE_REDIRECT_URI}?clientid={GOOGLE_CLIENT_ID}"


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for tokens."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data.get("expires_in", 3600),
    }


async def refresh_access_token(user_jid: str) -> str | None:
    """Refresh an expired access token. Returns new access token or None."""
    tokens = load_google_tokens(user_jid)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(TOKEN_URL, data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 401):
            log.warning("Refresh token revoked for %s, clearing tokens", user_jid)
            clear_google_tokens(user_jid)
            return None
        raise

    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data.get("expires_in", 3600)
    save_google_tokens(user_jid, tokens)
    return data["access_token"]


async def get_valid_token(user_jid: str) -> str | None:
    """Get a valid access token, refreshing if needed."""
    tokens = load_google_tokens(user_jid)
    if not tokens.get("refresh_token"):
        return None

    # Refresh if expired or within 60s of expiry
    if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]

    return await refresh_access_token(user_jid)


async def _authed_request(user_jid: str, method: str, url: str,
                         params: dict = None, json_body: dict = None) -> dict | str:
    """Make an authenticated Google API request with auto-retry on 401."""
    token = await get_valid_token(user_jid)
    if not token:
        return NOT_LINKED

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body

        resp = await getattr(client, method)(url, **kwargs)

        if resp.status_code == 401:
            token = await refresh_access_token(user_jid)
            if not token:
                return EXPIRED
            kwargs["headers"] = {"Authorization": f"Bearer {token}"}
            resp = await getattr(client, method)(url, **kwargs)

        resp.raise_for_status()
        return resp.json()


# ── Calendar ─────────────────────────────────────────────────────────

async def get_calendar_events(user_jid: str, start_date: str, end_date: str | None = None) -> list[dict] | str:
    """Fetch calendar events for a date range."""
    time_min = f"{start_date}T00:00:00Z"
    time_max = f"{(end_date or start_date)}T23:59:59Z"

    try:
        data = await _authed_request(user_jid, "get",
            f"{CALENDAR_API}/calendars/primary/events",
            params={
                "timeMin": time_min, "timeMax": time_max,
                "singleEvents": "true", "orderBy": "startTime", "maxResults": "25",
            })
    except httpx.HTTPError as e:
        log.error("Calendar API error: %s", e)
        return f"Failed to fetch calendar: {e}"

    if isinstance(data, str):
        return data  # error message

    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        events.append({
            "id": item.get("id", ""),
            "summary": item.get("summary", "(no title)"),
            "start": start.get("dateTime", start.get("date", "")),
            "end": item.get("end", {}).get("dateTime", ""),
            "location": item.get("location", ""),
        })
    return events


async def create_calendar_event(user_jid: str, summary: str, start_time: str,
                                end_time: str, location: str = "",
                                description: str = "") -> str:
    """Create a calendar event. Times in ISO format (e.g. 2026-04-07T15:00:00).
    For all-day events, use date format (e.g. 2026-04-07)."""
    is_allday = "T" not in start_time
    if is_allday:
        start_body = {"date": start_time}
        end_body = {"date": end_time}
    else:
        start_body = {"dateTime": start_time, "timeZone": "America/Los_Angeles"}
        end_body = {"dateTime": end_time, "timeZone": "America/Los_Angeles"}

    body = {
        "summary": summary,
        "start": start_body,
        "end": end_body,
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    try:
        data = await _authed_request(user_jid, "post",
            f"{CALENDAR_API}/calendars/primary/events", json_body=body)
    except httpx.HTTPError as e:
        log.error("Calendar create error: %s", e)
        return f"Failed to create event: {e}"

    if isinstance(data, str):
        return data
    return f"Event created: {summary}"


async def update_calendar_event(user_jid: str, event_id: str,
                                summary: str = "", start_time: str = "",
                                end_time: str = "", location: str = "") -> str:
    """Update an existing calendar event by ID. Only provided fields are changed."""
    body = {}
    if summary:
        body["summary"] = summary
    if start_time:
        if "T" not in start_time:
            body["start"] = {"date": start_time}
        else:
            body["start"] = {"dateTime": start_time, "timeZone": "America/Los_Angeles"}
    if end_time:
        if "T" not in end_time:
            body["end"] = {"date": end_time}
        else:
            body["end"] = {"dateTime": end_time, "timeZone": "America/Los_Angeles"}
    if location:
        body["location"] = location

    if not body:
        return "Nothing to update."

    try:
        data = await _authed_request(user_jid, "patch",
            f"{CALENDAR_API}/calendars/primary/events/{event_id}", json_body=body)
    except httpx.HTTPError as e:
        log.error("Calendar update error: %s", e)
        return f"Failed to update event: {e}"

    if isinstance(data, str):
        return data
    return f"Event updated: {data.get('summary', summary)}"


async def delete_calendar_event(user_jid: str, event_id: str) -> str:
    """Delete a calendar event by ID."""
    token = await get_valid_token(user_jid)
    if not token:
        return NOT_LINKED

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.delete(
                f"{CALENDAR_API}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                token = await refresh_access_token(user_jid)
                if not token:
                    return EXPIRED
                resp = await client.delete(
                    f"{CALENDAR_API}/calendars/primary/events/{event_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 204:
                return "Event deleted."
            resp.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Calendar delete error: %s", e)
        return f"Failed to delete event: {e}"
    return "Event deleted."


def format_events(events: list[dict], date_label: str = "today") -> str:
    if isinstance(events, str):
        return events
    if not events:
        return f"No events {date_label}."

    lines = [f"Calendar for {date_label}:\n"]
    for i, e in enumerate(events, 1):
        start = e["start"]
        if "T" in start:
            time_part = start.split("T")[1][:5]
        else:
            time_part = "All day"
        line = f"  {i}. {time_part} — {e['summary']}"
        if e.get("location"):
            line += f" ({e['location']})"
        lines.append(line)
    return "\n".join(lines)


# ── Gmail ────────────────────────────────────────────────────────────

async def get_unread_emails(user_jid: str, max_results: int = 10) -> list[dict] | str:
    """Fetch unread emails from inbox."""
    try:
        data = await _authed_request(user_jid, "get",
            f"{GMAIL_API}/messages",
            params={"q": "is:unread is:inbox", "maxResults": str(max_results)})
    except httpx.HTTPError as e:
        log.error("Gmail list error: %s", e)
        return f"Failed to fetch emails: {e}"

    if isinstance(data, str):
        return data

    messages = data.get("messages", [])
    if not messages:
        return []

    # Fetch headers for each message
    emails = []
    for msg in messages[:max_results]:
        try:
            detail = await _authed_request(user_jid, "get",
                f"{GMAIL_API}/messages/{msg['id']}",
                params={"format": "metadata", "metadataHeaders": "From,Subject,Date"})
            if isinstance(detail, str):
                continue
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
            })
        except Exception:
            continue
    return emails


async def get_email_body(user_jid: str, message_id: str) -> str:
    """Fetch the full text body of an email."""
    try:
        data = await _authed_request(user_jid, "get",
            f"{GMAIL_API}/messages/{message_id}",
            params={"format": "full"})
    except httpx.HTTPError as e:
        return f"Failed to read email: {e}"

    if isinstance(data, str):
        return data

    # Extract plain text body
    payload = data.get("payload", {})
    return _extract_text(payload) or data.get("snippet", "No content.")


def _extract_text(payload: dict) -> str:
    """Recursively extract text/plain from Gmail payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_text(part)
        if text:
            return text
    return ""


async def send_email(user_jid: str, to: str, subject: str, body: str) -> str:
    """Send an email. Returns success message or error."""
    token = await get_valid_token(user_jid)
    if not token:
        return NOT_LINKED

    # Strip newlines to prevent header injection
    to = to.replace("\n", "").replace("\r", "")
    subject = subject.replace("\n", " ").replace("\r", "")

    msg = MIMEText(body)
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{GMAIL_API}/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
            if resp.status_code == 401:
                token = await refresh_access_token(user_jid)
                if not token:
                    return EXPIRED
                resp = await client.post(
                    f"{GMAIL_API}/messages/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"raw": raw},
                )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Gmail send error: %s", e)
        return f"Failed to send email: {e}"

    return f"Email sent to {to}."


def format_emails(emails: list[dict]) -> str:
    if isinstance(emails, str):
        return emails
    if not emails:
        return "No unread emails."

    lines = ["Unread emails:\n"]
    for i, e in enumerate(emails, 1):
        sender = e["from"].split("<")[0].strip() or e["from"]
        lines.append(f"{i}. {sender}\n   {e['subject']}\n   {e['snippet'][:80]}")
    return "\n".join(lines)


# ── Tasks ────────────────────────────────────────────────────────────

async def get_task_lists(user_jid: str) -> list[dict] | str:
    """Get all task lists."""
    try:
        data = await _authed_request(user_jid, "get", f"{TASKS_API}/users/@me/lists")
    except httpx.HTTPError as e:
        log.error("Tasks API error: %s", e)
        return f"Failed to fetch task lists: {e}"

    if isinstance(data, str):
        return data
    return [{"id": t["id"], "title": t["title"]} for t in data.get("items", [])]


async def get_tasks(user_jid: str, tasklist_id: str = "@default") -> list[dict] | str:
    """Get tasks from a task list. Defaults to the primary list."""
    try:
        data = await _authed_request(user_jid, "get",
            f"{TASKS_API}/lists/{tasklist_id}/tasks",
            params={"showCompleted": "false", "maxResults": "20"})
    except httpx.HTTPError as e:
        log.error("Tasks API error: %s", e)
        return f"Failed to fetch tasks: {e}"

    if isinstance(data, str):
        return data

    tasks = []
    for t in data.get("items", []):
        if t.get("status") == "completed":
            continue
        tasks.append({
            "id": t["id"],
            "title": t.get("title", "(no title)"),
            "due": t.get("due", ""),
            "notes": t.get("notes", ""),
        })
    return tasks


async def add_task(user_jid: str, title: str, notes: str = "", due: str = "",
                   tasklist_id: str = "@default") -> str:
    """Add a task to a list. Returns confirmation or error."""
    body = {"title": title, "status": "needsAction"}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = f"{due}T00:00:00.000Z"

    try:
        data = await _authed_request(user_jid, "post",
            f"{TASKS_API}/lists/{tasklist_id}/tasks", json_body=body)
    except httpx.HTTPError as e:
        log.error("Tasks add error: %s", e)
        return f"Failed to add task: {e}"

    if isinstance(data, str):
        return data
    return f"Task added: {title}"


async def complete_task(user_jid: str, task_id: str, tasklist_id: str = "@default") -> str:
    """Mark a task as completed."""
    try:
        data = await _authed_request(user_jid, "patch",
            f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
            json_body={"status": "completed"})
    except httpx.HTTPError as e:
        log.error("Tasks complete error: %s", e)
        return f"Failed to complete task: {e}"

    if isinstance(data, str):
        return data
    return "Task completed."


def format_tasks(tasks: list[dict]) -> str:
    if isinstance(tasks, str):
        return tasks
    if not tasks:
        return "No pending tasks."

    lines = ["Your tasks:\n"]
    for i, t in enumerate(tasks, 1):
        line = f"{i}. {t['title']}"
        if t.get("due"):
            line += f" (due {t['due'][:10]})"
        if t.get("notes"):
            line += f"\n   {t['notes'][:60]}"
        lines.append(line)
    return "\n".join(lines)


# ── Contacts ─────────────────────────────────────────────────────────

async def search_contacts(user_jid: str, query: str) -> list[dict] | str:
    """Search Google Contacts by name or email."""
    try:
        data = await _authed_request(user_jid, "get",
            f"{PEOPLE_API}/people:searchContacts",
            params={"query": query, "readMask": "names,emailAddresses,phoneNumbers", "pageSize": "10"})
    except httpx.HTTPError as e:
        log.error("Contacts API error: %s", e)
        return f"Failed to search contacts: {e}"

    if isinstance(data, str):
        return data

    contacts = []
    for result in data.get("results", []):
        person = result.get("person", {})
        names = person.get("names", [{}])
        emails = person.get("emailAddresses", [])
        phones = person.get("phoneNumbers", [])
        contacts.append({
            "name": names[0].get("displayName", "") if names else "",
            "email": emails[0].get("value", "") if emails else "",
            "phone": phones[0].get("value", "") if phones else "",
        })
    return contacts


def format_contacts(contacts: list[dict]) -> str:
    if isinstance(contacts, str):
        return contacts
    if not contacts:
        return "No contacts found."

    lines = ["Contacts:\n"]
    for c in contacts:
        line = c["name"]
        if c.get("email"):
            line += f" — {c['email']}"
        if c.get("phone"):
            line += f" — {c['phone']}"
        lines.append(f"  {line}")
    return "\n".join(lines)


# ── Drive ────────────────────────────────────────────────────────────

async def search_drive(user_jid: str, query: str) -> list[dict] | str:
    """Search Google Drive files by name or content."""
    # Escape single quotes in query for Drive API
    safe_q = query.replace("'", "\\'")
    try:
        data = await _authed_request(user_jid, "get",
            f"{DRIVE_API}/files",
            params={
                "q": f"fullText contains '{safe_q}' and trashed = false",
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
                "pageSize": "10",
                "orderBy": "modifiedTime desc",
            })
    except httpx.HTTPError as e:
        log.error("Drive API error: %s", e)
        return f"Failed to search Drive: {e}"

    if isinstance(data, str):
        return data

    files = []
    for f in data.get("files", []):
        files.append({
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "type": _drive_type(f.get("mimeType", "")),
            "modified": f.get("modifiedTime", "")[:10],
            "link": f.get("webViewLink", ""),
            "size": _format_size(f.get("size")),
        })
    return files


async def list_recent_drive(user_jid: str, max_results: int = 10) -> list[dict] | str:
    """List recently modified Google Drive files."""
    try:
        data = await _authed_request(user_jid, "get",
            f"{DRIVE_API}/files",
            params={
                "q": "trashed = false",
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
                "pageSize": str(min(max_results, 20)),
                "orderBy": "modifiedTime desc",
            })
    except httpx.HTTPError as e:
        log.error("Drive API error: %s", e)
        return f"Failed to list Drive files: {e}"

    if isinstance(data, str):
        return data

    files = []
    for f in data.get("files", []):
        files.append({
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "type": _drive_type(f.get("mimeType", "")),
            "modified": f.get("modifiedTime", "")[:10],
            "link": f.get("webViewLink", ""),
            "size": _format_size(f.get("size")),
        })
    return files


def _drive_type(mime: str) -> str:
    """Convert MIME type to friendly name."""
    types = {
        "application/vnd.google-apps.document": "Doc",
        "application/vnd.google-apps.spreadsheet": "Sheet",
        "application/vnd.google-apps.presentation": "Slides",
        "application/vnd.google-apps.folder": "Folder",
        "application/vnd.google-apps.form": "Form",
        "application/pdf": "PDF",
        "image/": "Image",
        "video/": "Video",
        "text/": "Text",
    }
    for prefix, label in types.items():
        if mime.startswith(prefix):
            return label
    return "File"


def _format_size(size_str) -> str:
    if not size_str:
        return ""
    size = int(size_str)
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size // (1024 * 1024)}MB"


def format_drive_files(files: list[dict]) -> str:
    if isinstance(files, str):
        return files
    if not files:
        return "No files found."

    lines = ["Drive files:\n"]
    for i, f in enumerate(files, 1):
        line = f"{i}. [{f['type']}] {f['name']}"
        if f.get("modified"):
            line += f" — {f['modified']}"
        if f.get("size"):
            line += f" ({f['size']})"
        lines.append(line)
    return "\n".join(lines)


MAX_DRIVE_TEXT = 4000  # max chars to return to LLM

# Google Workspace MIME → export format
_EXPORT_MAP = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}


async def read_drive_file(user_jid: str, file_id: str, mime_type: str = "",
                          file_name: str = "") -> str:
    """Download and extract text content from a Drive file.
    Supports Google Docs/Sheets/Slides, PDFs, images, and text files.
    Returns extracted text or error string."""
    token = await get_valid_token(user_jid)
    if not token:
        return NOT_LINKED

    # If we don't know the mime type, fetch file metadata
    if not mime_type:
        try:
            meta = await _authed_request(user_jid, "get",
                f"{DRIVE_API}/files/{file_id}",
                params={"fields": "mimeType,name"})
            if isinstance(meta, str):
                return meta
            mime_type = meta.get("mimeType", "")
            file_name = meta.get("name", file_name)
        except httpx.HTTPError as e:
            return f"Failed to get file info: {e}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": f"Bearer {token}"}

            # Google Workspace files: export
            if mime_type in _EXPORT_MAP:
                export_mime, _ = _EXPORT_MAP[mime_type]
                resp = await client.get(
                    f"{DRIVE_API}/files/{file_id}/export",
                    headers=headers,
                    params={"mimeType": export_mime},
                )
            else:
                # Binary/regular files: download
                resp = await client.get(
                    f"{DRIVE_API}/files/{file_id}",
                    headers=headers,
                    params={"alt": "media"},
                )

            if resp.status_code == 401:
                token = await refresh_access_token(user_jid)
                if not token:
                    return EXPIRED
                headers = {"Authorization": f"Bearer {token}"}
                if mime_type in _EXPORT_MAP:
                    export_mime, _ = _EXPORT_MAP[mime_type]
                    resp = await client.get(
                        f"{DRIVE_API}/files/{file_id}/export",
                        headers=headers,
                        params={"mimeType": export_mime},
                    )
                else:
                    resp = await client.get(
                        f"{DRIVE_API}/files/{file_id}",
                        headers=headers,
                        params={"alt": "media"},
                    )

            resp.raise_for_status()
            raw = resp.content

    except httpx.HTTPError as e:
        log.error("Drive download error: %s", e)
        return f"Failed to download file: {e}"

    # Extract text based on type
    result = _extract_drive_content(raw, mime_type, file_name)

    # Handle images async via vision model
    if result.startswith("__IMAGE__:"):
        from agent.services.llm import vision_completion
        img_b64 = base64.b64encode(raw).decode("utf-8")
        description = await vision_completion(img_b64, "Describe this image in detail.")
        return f"[{file_name}]\n\n{description}"

    return result


def _extract_drive_content(raw: bytes, mime_type: str, file_name: str) -> str:
    """Extract text from downloaded file bytes."""
    import tempfile
    import os

    # Google Workspace exports come as text already
    if mime_type in _EXPORT_MAP:
        text = raw.decode("utf-8", errors="replace")
        if len(text) > MAX_DRIVE_TEXT:
            text = text[:MAX_DRIVE_TEXT] + f"\n\n[Truncated — showing first {MAX_DRIVE_TEXT} chars]"
        return f"[{file_name}]\n\n{text}"

    # PDF
    if mime_type == "application/pdf":
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(raw)
                tmp = f.name
            try:
                doc = fitz.open(tmp)
                pages = [page.get_text() for page in doc]
                doc.close()
                text = "\n\n".join(pages)
            finally:
                os.unlink(tmp)
            if len(text) > MAX_DRIVE_TEXT:
                text = text[:MAX_DRIVE_TEXT] + f"\n\n[Truncated — showing first {MAX_DRIVE_TEXT} chars]"
            return f"[{file_name}]\n\n{text}"
        except Exception as e:
            return f"Failed to read PDF: {e}"

    # Images — return marker, caller should use read_drive_file_with_vision instead
    if mime_type.startswith("image/"):
        return f"__IMAGE__:{file_name}"

    # Text-based files
    if mime_type.startswith("text/") or mime_type in (
        "application/json", "application/xml", "application/javascript",
    ):
        text = raw.decode("utf-8", errors="replace")
        if len(text) > MAX_DRIVE_TEXT:
            text = text[:MAX_DRIVE_TEXT] + f"\n\n[Truncated — showing first {MAX_DRIVE_TEXT} chars]"
        return f"[{file_name}]\n\n{text}"

    return f"[{file_name}] Unsupported file type: {mime_type}. Cannot extract text."


async def read_drive_file_with_vision(user_jid: str, file_id: str,
                                       mime_type: str, file_name: str) -> str:
    """Read a Drive image file using the vision model (async-safe)."""
    token = await get_valid_token(user_jid)
    if not token:
        return NOT_LINKED

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"alt": "media"},
            )
            resp.raise_for_status()
            raw = resp.content
    except httpx.HTTPError as e:
        return f"Failed to download image: {e}"

    from agent.services.llm import vision_completion
    img_b64 = base64.b64encode(raw).decode("utf-8")
    description = await vision_completion(img_b64, "Describe this image in detail.")
    return f"[{file_name}]\n\n{description}"


