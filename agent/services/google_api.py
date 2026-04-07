"""Google OAuth2 and Calendar API via httpx."""
from __future__ import annotations
import time
import logging
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
])
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TIMEOUT = 10


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


async def get_calendar_events(user_jid: str, start_date: str, end_date: str | None = None) -> list[dict] | str:
    """Fetch calendar events for a date range. Returns list of events or error string."""
    token = await get_valid_token(user_jid)
    if not token:
        return "Google account not linked. Use /google link to connect."

    # Build time range
    time_min = f"{start_date}T00:00:00Z"
    if end_date:
        time_max = f"{end_date}T23:59:59Z"
    else:
        time_max = f"{start_date}T23:59:59Z"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{CALENDAR_API}/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": "25",
                },
            )
            if resp.status_code == 401:
                # Token might be stale, try refresh once
                token = await refresh_access_token(user_jid)
                if not token:
                    return "Google access expired. Use /google link to reconnect."
                resp = await client.get(
                    f"{CALENDAR_API}/calendars/primary/events",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": "25",
                    },
                )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        log.error("Calendar API error: %s", e)
        return f"Failed to fetch calendar: {e}"

    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        events.append({
            "summary": item.get("summary", "(no title)"),
            "start": start.get("dateTime", start.get("date", "")),
            "end": item.get("end", {}).get("dateTime", ""),
            "location": item.get("location", ""),
        })
    return events


def format_events(events: list[dict], date_label: str = "today") -> str:
    """Format calendar events for WhatsApp display."""
    if isinstance(events, str):
        return events  # error message
    if not events:
        return f"No events {date_label}."

    lines = [f"Calendar for {date_label}:\n"]
    for e in events:
        start = e["start"]
        # Extract time from ISO datetime
        if "T" in start:
            time_part = start.split("T")[1][:5]
        else:
            time_part = "All day"

        line = f"  {time_part} — {e['summary']}"
        if e.get("location"):
            line += f" ({e['location']})"
        lines.append(line)
    return "\n".join(lines)
