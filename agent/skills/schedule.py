"""Schedule skill — create, list, and remove scheduled tasks."""
from __future__ import annotations
import re
import logging
from agent.services.scheduler import add_schedule, list_schedules, remove_schedule
from agent.tools import web_search
from agent.services.llm import chat_completion
from agent.config import SYSTEM_PROMPT

log = logging.getLogger(__name__)

WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

TASK_TYPES = {
    "news": "news",
    "search": "search",
    "podcast": "podcast",
}


def _parse_time(text: str) -> tuple[int, int] | None:
    """Parse time from text like '4pm', '16:00', '4:30pm', '14:30'."""
    # 4pm, 4:30pm, 4:30 pm
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.I)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2) or 0)
        if m.group(3).lower() == "pm" and h != 12:
            h += 12
        if m.group(3).lower() == "am" and h == 12:
            h = 0
        return h, mins

    # 16:00, 14:30
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    return None


def _parse_schedule_args(args: str) -> dict | None:
    """Parse schedule command arguments.

    Formats:
        /schedule news daily 4pm AI technology
        /schedule search weekly monday 9am weather forecast
        /schedule podcast daily 8am tech news
        /schedule news daily 4pm --audio AI technology
    """
    parts = args.strip().split()
    if len(parts) < 3:
        return None

    result = {"audio": False}

    # Task type
    task_type = parts[0].lower()
    if task_type not in TASK_TYPES:
        return None
    result["task_type"] = TASK_TYPES[task_type]

    # Frequency
    freq = parts[1].lower()
    if freq not in ("daily", "weekly"):
        return None
    result["frequency"] = freq

    rest = parts[2:]

    # For weekly: parse weekday
    if freq == "weekly":
        if not rest:
            return None
        day = rest[0].lower()
        if day not in WEEKDAYS:
            return None
        result["weekday"] = WEEKDAYS[day]
        rest = rest[1:]

    # Parse time
    if not rest:
        return None
    time_str = rest[0]
    parsed = _parse_time(time_str)
    if not parsed:
        return None
    result["hour"], result["minute"] = parsed
    rest = rest[1:]

    # Check for --audio flag
    if "--audio" in rest:
        result["audio"] = True
        rest = [r for r in rest if r != "--audio"]

    # Remaining is the topic/query
    result["topic"] = " ".join(rest)
    if not result["topic"]:
        return None

    return result


async def schedule_add(state: dict) -> dict:
    """Handle /schedule command."""
    args = state.get("intent_args", "").strip()
    user_jid = state.get("user_jid", "")

    if not args:
        return {"reply_text": (
            "Usage: /schedule <type> <frequency> [day] <time> [--audio] <topic>\n\n"
            "Types: news, search, podcast\n"
            "Frequency: daily, weekly\n\n"
            "Examples:\n"
            "  /schedule news daily 4pm AI technology\n"
            "  /schedule podcast daily 8am --audio tech news\n"
            "  /schedule search weekly monday 9am weather forecast"
        )}

    parsed = _parse_schedule_args(args)
    if not parsed:
        return {"reply_text": (
            "Couldn't parse schedule. Format:\n"
            "/schedule <news|search|podcast> <daily|weekly> [day] <time> [--audio] <topic>"
        )}

    task = add_schedule(
        user_jid=user_jid,
        task_type=parsed["task_type"],
        task_args=parsed["topic"],
        hour=parsed["hour"],
        minute=parsed.get("minute", 0),
        frequency=parsed["frequency"],
        weekday=parsed.get("weekday"),
        audio=parsed["audio"],
    )

    time_str = f"{task['hour']:02d}:{task['minute']:02d}"
    freq_str = task["frequency"]
    if freq_str == "weekly":
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        freq_str = f"weekly on {days[task['weekday']]}"

    return {"reply_text": (
        f"Scheduled! ID: {task['id']}\n"
        f"Type: {task['task_type']}\n"
        f"Topic: {task['task_args']}\n"
        f"When: {freq_str} at {time_str}\n"
        f"Audio: {'yes' if task['audio'] else 'no'}"
    )}


async def schedule_list(state: dict) -> dict:
    """Handle /schedules command."""
    user_jid = state.get("user_jid", "")
    tasks = list_schedules(user_jid)

    if not tasks:
        return {"reply_text": "No scheduled tasks. Use /schedule to create one."}

    lines = ["Your scheduled tasks:\n"]
    for t in tasks:
        time_str = f"{t['hour']:02d}:{t['minute']:02d}"
        freq = t["frequency"]
        if freq == "weekly" and t.get("weekday") is not None:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            freq = f"weekly/{days[t['weekday']]}"
        audio = " 🎙" if t.get("audio") else ""
        lines.append(f"  [{t['id']}] {t['task_type']} — {t['task_args']} — {freq} {time_str}{audio}")

    return {"reply_text": "\n".join(lines)}


async def schedule_remove(state: dict) -> dict:
    """Handle /unschedule command."""
    args = state.get("intent_args", "").strip()
    user_jid = state.get("user_jid", "")

    if not args:
        return {"reply_text": "Usage: /unschedule <id>\nUse /schedules to see your task IDs."}

    if remove_schedule(user_jid, args):
        return {"reply_text": f"Schedule {args} removed."}
    return {"reply_text": f"Schedule {args} not found."}


# --- Task handlers (called by the scheduler) ---

async def _handle_news(task: dict) -> str:
    """Fetch news on a topic and summarize."""
    topic = task["task_args"]
    results = await web_search(f"{topic} latest news today")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Summarize the latest news about: {topic}\n\n"
                f"Search results:\n{results}\n\n"
                "Give a concise briefing with the key headlines and developments."
            ),
        },
    ]
    return await chat_completion(messages)


async def _handle_search(task: dict) -> str:
    """Search for a topic and summarize."""
    query = task["task_args"]
    results = await web_search(query)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"The user wants an update on: {query}\n\n"
                f"Search results:\n{results}\n\n"
                "Provide a helpful, concise summary."
            ),
        },
    ]
    return await chat_completion(messages)


async def _handle_podcast(task: dict) -> str:
    """Generate a podcast script on a topic."""
    from agent.skills.podcast import PODCAST_SCRIPT_PROMPT
    from agent.tools import read_page

    topic = task["task_args"]
    results = await web_search(f"{topic} latest news today")

    # Read top pages
    urls = [line.strip() for line in results.split("\n") if line.strip().startswith("http")]
    page_contents = []
    for url in urls[:2]:
        content = await read_page(url)
        if content and not content.startswith("Failed"):
            page_contents.append(content[:3000])

    research = f"SEARCH RESULTS:\n{results}\n\n"
    if page_contents:
        research += "DETAILED CONTENT:\n\n"
        for i, c in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{c}\n\n"

    messages = [
        {"role": "system", "content": PODCAST_SCRIPT_PROMPT},
        {"role": "user", "content": f"Topic: {topic}\n\n{research}\n\nWrite the podcast script now."},
    ]
    return await chat_completion(messages, max_tokens=2048)
