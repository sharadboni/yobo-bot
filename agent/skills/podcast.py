"""Podcast skill — research a topic and generate a voice-note podcast."""
from __future__ import annotations
import logging
from agent.tools import web_search, read_page
from agent.services.llm import chat_completion

log = logging.getLogger(__name__)

PODCAST_SCRIPT_PROMPT = """\
You are a podcast script writer. Based on the research material below, write a \
natural, engaging podcast monologue about the topic.

Rules:
- Write as spoken word — conversational, not formal. Use contractions, rhetorical \
questions, and transitions like "so", "now", "here's the thing".
- Keep it concise: 2-4 minutes when read aloud (roughly 300-600 words).
- Start with a hook, cover the key points, end with a takeaway.
- Do NOT include stage directions, speaker labels, or [brackets].
- Do NOT include any markdown formatting.
- Just the spoken text, ready for text-to-speech.
"""

MAX_PAGES_TO_READ = 3


async def podcast(state: dict) -> dict:
    topic = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not topic:
        return {"reply_text": "Please provide a topic. Usage: /podcast <topic>"}

    # Step 1: Search for the topic
    log.info("[podcast] Searching for: %s", topic)
    search_results = await web_search(topic)

    if search_results.startswith("All search providers failed"):
        return {"reply_text": "Couldn't find information on that topic. Try again later."}

    # Step 2: Read the top pages for deeper content
    # Extract URLs from the formatted search results
    urls = []
    for line in search_results.split("\n"):
        line = line.strip()
        if line.startswith("http"):
            urls.append(line)

    page_contents = []
    for url in urls[:MAX_PAGES_TO_READ]:
        log.info("[podcast] Reading: %s", url)
        content = await read_page(url)
        if content and not content.startswith("Failed"):
            page_contents.append(content)

    # Step 3: Build research material
    research = f"SEARCH RESULTS:\n{search_results}\n\n"
    if page_contents:
        research += "DETAILED CONTENT FROM TOP RESULTS:\n\n"
        for i, content in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{content[:3000]}\n\n"

    # Step 4: Generate podcast script
    messages = [
        {"role": "system", "content": PODCAST_SCRIPT_PROMPT},
        {
            "role": "user",
            "content": f"Topic: {topic}\n\n{research}\n\nWrite the podcast script now.",
        },
    ]

    log.info("[podcast] Generating script...")
    script = await chat_completion(messages, max_tokens=2048)

    # Return with force_audio flag so main.py always generates TTS
    return {"reply_text": script, "content_type": "audio"}
