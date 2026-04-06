"""Scheduler task handlers — synthesize user messages for the pipeline."""
from __future__ import annotations
import logging
import httpx

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


_CHUNK_SIZE = 4000


def _split_text(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into chunks, breaking at the last newline before the limit."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        # Find last newline within the chunk
        cut = text.rfind("\n", 0, chunk_size)
        if cut <= 0:
            cut = chunk_size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def handle_webhook(task: dict) -> list[dict] | dict | None:
    """Fetch a URL and send the response as one or more messages."""
    url = task["task_args"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:
        log.warning("Webhook %s failed: %s", url, e)
        text = f"Scheduled fetch failed: {e}"

    audio = task.get("audio", False)
    chunks = _split_text(text)
    if len(chunks) == 1:
        return _make_message_payload(task["user_jid"], chunks[0], force_audio=audio)
    return [_make_message_payload(task["user_jid"], c, force_audio=audio) for c in chunks]
