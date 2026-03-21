"""Scheduler task handlers — executed by the background scheduler, not the pipeline."""
from __future__ import annotations
import logging
from agent.tools import web_search, news_search, read_page
from agent.services.llm import chat_completion
from agent.config import SYSTEM_PROMPT
from agent.skills.podcast import PODCAST_SCRIPT_PROMPT

log = logging.getLogger(__name__)


async def handle_news(task: dict) -> str:
    """Fetch news on a topic and summarize."""
    topic = task["task_args"]
    results = await news_search(topic)

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
    return await chat_completion(messages, no_think=True)


async def handle_search(task: dict) -> str:
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
    return await chat_completion(messages, no_think=True)


async def handle_podcast(task: dict) -> str:
    """Generate a podcast script on a topic."""
    topic = task["task_args"]
    results = await web_search(f"{topic} latest news today")

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
    return await chat_completion(messages, max_tokens=2048, no_think=True)
