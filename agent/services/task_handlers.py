"""Scheduler task handlers — synthesize user messages for the pipeline."""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def _make_message_payload(user_jid: str, text: str, force_audio: bool = False) -> dict:
    """Create a synthetic inbound message payload for the pipeline."""
    return {
        "type": "message",
        "from": user_jid,
        "pushName": "",
        "content": {"type": "text", "text": text},
        "scheduled": True,
        "force_audio": force_audio,
    }


async def handle_news(task: dict) -> dict:
    topic = task["task_args"]
    return _make_message_payload(
        task["user_jid"],
        f"/news {topic}",
        force_audio=task.get("audio", False),
    )


async def handle_search(task: dict) -> dict:
    query = task["task_args"]
    return _make_message_payload(
        task["user_jid"],
        f"/search {query}",
        force_audio=task.get("audio", False),
    )


async def handle_podcast(task: dict) -> dict:
    topic = task["task_args"]
    return _make_message_payload(
        task["user_jid"],
        f"/podcast {topic}",
        force_audio=True,  # podcasts always have audio
    )
