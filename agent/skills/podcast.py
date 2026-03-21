"""Podcast skill — research a topic and generate a voice-note podcast."""
from __future__ import annotations
import logging
from agent.tools import news_search, web_search, read_page
from agent.services.llm import chat_completion

log = logging.getLogger(__name__)

PODCAST_SCRIPT_PROMPT = """\
You are a podcast host speaking directly to a listener. Based on the research \
material below, deliver a natural spoken monologue about the topic.

Rules:
- Write EXACTLY as you would speak out loud. No written formatting at all.
- Use contractions, rhetorical questions, pauses, and natural transitions.
- STRICT LENGTH: Maximum 200 words. This is absolutely critical.
- Start with a hook, cover key points, end with a takeaway.
- FORBIDDEN: No asterisks, no bold, no bullet points, no headers, no markdown, \
no brackets, no parenthetical notes, no speaker labels, no stage directions.
- Output ONLY the spoken words. Nothing else before or after.
"""

CONDENSE_PROMPT = """\
The following podcast script is too long. Condense it to under 200 words while \
keeping it natural and conversational. Keep the hook and takeaway. \
Do not add any formatting — output ONLY the spoken words."""

MAX_WORDS = 200
MAX_RETRIES = 2
MAX_PAGES_TO_READ = 2


async def podcast(state: dict) -> dict:
    topic = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not topic:
        return {"reply_text": "Please provide a topic. Usage: /podcast <topic>"}

    # Step 1: Try news first, fall back to web search
    log.info("[podcast] Searching for: %s", topic)
    search_results = await news_search(topic)

    if not search_results or search_results.startswith("All search providers failed"):
        search_results = await web_search(topic)

    if search_results.startswith("All search providers failed"):
        return {"reply_text": "Couldn't find information on that topic. Try again later."}

    # Step 2: Read top pages for deeper content
    urls = [line.strip() for line in search_results.split("\n") if line.strip().startswith("http")]
    page_contents = []
    for url in urls[:MAX_PAGES_TO_READ]:
        log.info("[podcast] Reading: %s", url)
        content = await read_page(url)
        if content and not content.startswith("Failed") and not content.startswith("Cannot"):
            page_contents.append(content[:2000])

    # Step 3: Build research material
    research = f"SEARCH RESULTS:\n{search_results}\n\n"
    if page_contents:
        research += "DETAILED CONTENT:\n\n"
        for i, content in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{content}\n\n"

    # Step 4: Generate podcast script
    log.info("[podcast] Generating script...")
    script = await chat_completion([
        {"role": "system", "content": PODCAST_SCRIPT_PROMPT},
        {"role": "user", "content": f"Topic: {topic}\n\n{research}\n\nSpeak now."},
    ], max_tokens=512, no_think=True)

    # Step 5: If too long, ask LLM to condense (retry up to MAX_RETRIES times)
    word_count = len(script.split())
    for attempt in range(MAX_RETRIES):
        if word_count <= MAX_WORDS:
            break
        log.info("[podcast] Script too long (%d words), condensing (attempt %d)...", word_count, attempt + 1)
        script = await chat_completion([
            {"role": "system", "content": CONDENSE_PROMPT},
            {"role": "user", "content": script},
        ], max_tokens=512, no_think=True)
        word_count = len(script.split())

    if word_count > MAX_WORDS:
        log.warning("[podcast] Script still %d words after %d condense attempts", word_count, MAX_RETRIES)

    log.info("[podcast] Script ready: %d words, %d chars", word_count, len(script))
    return {"reply_text": script, "content_type": "audio"}
