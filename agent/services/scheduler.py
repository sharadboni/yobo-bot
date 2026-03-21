"""Scheduler service — persistent cron-like task scheduling per user."""
from __future__ import annotations
import asyncio
import json
import os
import fcntl
import time
import logging
from datetime import datetime, timedelta
from uuid import uuid4

log = logging.getLogger(__name__)

SCHEDULES_DIR = os.getenv("SCHEDULES_DIR", "data/schedules")

# Task type → async handler, registered at startup
_task_handlers: dict = {}

# Outbound message queue — scheduler pushes, main.py drains and sends
outbound_queue: asyncio.Queue = asyncio.Queue()


def register_handler(task_type: str, handler):
    """Register an async handler for a task type. handler(task) -> str"""
    _task_handlers[task_type] = handler


def _user_schedule_path(user_jid: str) -> str:
    """Per-user schedule file: data/schedules/<number>.json"""
    from agent.jid import jid_to_number
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    return os.path.join(SCHEDULES_DIR, f"{jid_to_number(user_jid)}.json")


def _load_user(user_jid: str) -> list[dict]:
    path = _user_schedule_path(user_jid)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data


def _save_user(user_jid: str, schedules: list[dict]) -> None:
    path = _user_schedule_path(user_jid)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(schedules, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def _load_all_users() -> list[dict]:
    """Load schedules from all user files (for the scheduler loop)."""
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    all_tasks = []
    for fname in os.listdir(SCHEDULES_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SCHEDULES_DIR, fname)
        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                tasks = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            all_tasks.extend(tasks)
        except Exception as e:
            log.warning("Failed to load schedule %s: %s", fname, e)
    return all_tasks


def add_schedule(
    user_jid: str,
    task_type: str,
    task_args: str,
    hour: int,
    minute: int = 0,
    frequency: str = "daily",  # daily | weekly
    weekday: int | None = None,  # 0=Mon, 6=Sun (for weekly)
    audio: bool = False,
) -> dict:
    """Add a new scheduled task. Returns the task dict."""
    task = {
        "id": uuid4().hex[:8],
        "user_jid": user_jid,
        "task_type": task_type,
        "task_args": task_args,
        "hour": hour,
        "minute": minute,
        "frequency": frequency,
        "weekday": weekday,
        "audio": audio,
        "created_at": time.time(),
        "last_run": None,
    }
    schedules = _load_user(user_jid)
    schedules.append(task)
    _save_user(user_jid, schedules)
    log.info("Schedule added: %s for %s at %02d:%02d %s",
             task["id"], user_jid, hour, minute, frequency)
    return task


def list_schedules(user_jid: str) -> list[dict]:
    """List all schedules for a user (reads only their file)."""
    return _load_user(user_jid)


def remove_schedule(user_jid: str, schedule_id: str) -> bool:
    """Remove a schedule by ID. Only searches the user's own file."""
    schedules = _load_user(user_jid)
    before = len(schedules)
    schedules = [s for s in schedules if s["id"] != schedule_id]
    if len(schedules) == before:
        return False
    _save_user(user_jid, schedules)
    log.info("Schedule removed: %s", schedule_id)
    return True


def _is_due(task: dict, now: datetime) -> bool:
    """Check if a task should run now (within this minute)."""
    if task["hour"] != now.hour or task["minute"] != now.minute:
        return False

    if task["frequency"] == "weekly" and task.get("weekday") is not None:
        if now.weekday() != task["weekday"]:
            return False

    # Don't run if already ran this minute
    if task["last_run"]:
        last = datetime.fromtimestamp(task["last_run"])
        if last.date() == now.date() and last.hour == now.hour and last.minute == now.minute:
            return False

    return True


async def _execute_task(task: dict):
    """Execute a scheduled task and send results to the user."""
    task_type = task["task_type"]
    handler = _task_handlers.get(task_type)
    if not handler:
        log.warning("No handler for task type: %s", task_type)
        return

    try:
        log.info("Executing scheduled task %s (%s) for %s",
                 task["id"], task_type, task["user_jid"])
        result_text = await handler(task)
        if not result_text:
            return

        reply_msg = {
            "type": "reply",
            "to": task["user_jid"],
            "content": {"text": f"Scheduled update — {task['task_args']}:\n\n{result_text}"},
        }

        # Generate audio if requested
        if task.get("audio"):
            try:
                from agent.services.llm import synthesize_speech
                import base64
                audio_bytes, mimetype = await synthesize_speech(result_text)
                reply_msg["content"]["audio"] = base64.b64encode(audio_bytes).decode()
                reply_msg["content"]["audio_mimetype"] = mimetype
            except Exception as e:
                log.warning("Scheduled TTS failed: %s", e)

        await outbound_queue.put(reply_msg)
    except Exception as e:
        log.error("Scheduled task %s failed: %s", task["id"], e, exc_info=True)


async def run_scheduler():
    """Background loop — checks every 30s for due tasks."""
    log.info("Scheduler started")
    while True:
        try:
            now = datetime.now()
            all_tasks = _load_all_users()
            # Group modified tasks by user for saving
            modified_users = set()

            for task in all_tasks:
                if _is_due(task, now):
                    await _execute_task(task)
                    task["last_run"] = time.time()
                    modified_users.add(task["user_jid"])

            # Save only modified users' files
            for user_jid in modified_users:
                user_tasks = [t for t in all_tasks if t["user_jid"] == user_jid]
                _save_user(user_jid, user_tasks)
        except Exception as e:
            log.error("Scheduler error: %s", e, exc_info=True)

        await asyncio.sleep(30)
