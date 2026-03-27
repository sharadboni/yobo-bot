"""Scheduler task handlers — executed by the background scheduler, not the pipeline."""
from __future__ import annotations
import logging
from agent.tools import web_search, news_search, read_page
from agent.services.llm import chat_completion
from agent.config import get_system_prompt_tools
from agent.constants import (
    MAX_TOKENS_SCHEDULED_NEWS, MAX_TOKENS_SCHEDULED_SEARCH, MAX_TOKENS_SCHEDULED_PODCAST,
    TEMP_SCHEDULED, TEMP_PODCAST_SCRIPT,
)
from agent.skills.podcast import PODCAST_SCRIPT_PROMPT

log = logging.getLogger(__name__)

MAX_PAGES_TO_READ = 3


async def handle_news(task: dict) -> str:
    """Fetch news on a topic and summarize — same pipeline as chat tool calling."""
    topic = task["task_args"]

    # Same research as the chat path: aggregated news + page reads
    results = await news_search(topic)

    urls = [line.strip() for line in results.split("\n") if line.strip().startswith("http")]
    page_contents = []
    for url in urls[:MAX_PAGES_TO_READ]:
        content = await read_page(url)
        if content and not content.startswith("Failed") and not content.startswith("Cannot"):
            page_contents.append(content[:2000])

    research = f"News results:\n{results}\n\n"
    if page_contents:
        research += "Detailed content:\n\n"
        for i, c in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{c}\n\n"

    # Same system prompt as the chat tool-calling path
    messages = [
        {"role": "system", "content": get_system_prompt_tools()},
        {
            "role": "user",
            "content": (
                f"Give me a news briefing about: {topic}\n\n"
                f"{research}\n"
                "Summarize each key story with a brief description."
            ),
        },
    ]
    return await chat_completion(messages, max_tokens=MAX_TOKENS_SCHEDULED_NEWS, temperature=TEMP_SCHEDULED, no_think=True)


async def handle_search(task: dict) -> str:
    """Search for a topic and summarize."""
    query = task["task_args"]
    results = await web_search(query)

    urls = [line.strip() for line in results.split("\n") if line.strip().startswith("http")]
    page_contents = []
    for url in urls[:MAX_PAGES_TO_READ]:
        content = await read_page(url)
        if content and not content.startswith("Failed") and not content.startswith("Cannot"):
            page_contents.append(content[:2000])

    research = f"Search results:\n{results}\n\n"
    if page_contents:
        research += "Detailed content:\n\n"
        for i, c in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{c}\n\n"

    messages = [
        {"role": "system", "content": get_system_prompt_tools()},
        {
            "role": "user",
            "content": (
                f"Give me an update on: {query}\n\n"
                f"{research}\n"
                "Provide a helpful summary."
            ),
        },
    ]
    return await chat_completion(messages, max_tokens=MAX_TOKENS_SCHEDULED_SEARCH, temperature=TEMP_SCHEDULED, no_think=True)


async def handle_podcast(task: dict) -> str:
    """Generate a podcast script on a topic."""
    from agent.skills.podcast import _research

    topic = task["task_args"]
    research = await _research(topic)
    if not research:
        return f"Couldn't find information on {topic}."

    messages = [
        {"role": "system", "content": PODCAST_SCRIPT_PROMPT},
        {"role": "user", "content": f"Topic: {topic}\n\n{research}\n\nSpeak now."},
    ]
    return await chat_completion(messages, max_tokens=MAX_TOKENS_SCHEDULED_PODCAST, temperature=TEMP_PODCAST_SCRIPT, no_think=True)
