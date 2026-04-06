"""News skill — fetch and summarize news from multiple sources."""
from __future__ import annotations
import re
import logging
from agent.tools import news_search_aggregated, SOURCE_ALIASES
from agent.services.llm import chat_completion
from agent.constants import MAX_TOKENS_TOOL_ANSWER, TEMP_TOOL_ANSWER

log = logging.getLogger(__name__)

_NEWS_SYSTEM = (
    "You are Yobo, a WhatsApp news assistant. Today is {date}.\n"
    "Summarize news results into a concise briefing. Rules:\n"
    "- Only use stories from the provided results. Never fabricate.\n"
    "- Provide exactly {count} stories (or fewer if not enough relevant results).\n"
    "- Each story: numbered, 3-4 sentence summary covering key facts, context, and why it matters.\n"
    "- Merge duplicate stories. Skip irrelevant or outdated results.\n"
    "- Plain text only. No markdown, no bold, no asterisks.\n"
    "- Reply in the user's language.\n"
    "- No preamble or disclaimers. Jump straight into the stories."
)

# Match leading number words or digits in the query like "top 3", "5 latest", "ten"
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT_RE = re.compile(
    r"(?:top|latest|recent|last)?\s*(\d+|" + "|".join(_NUM_WORDS) + r")\b",
    re.IGNORECASE,
)
_DEFAULT_COUNT = 10

# --from <source> or --source <source>
_SOURCE_RE = re.compile(r"--(?:from|source)\s+(\S+)", re.IGNORECASE)


def _extract_count(topic: str) -> int:
    """Extract requested story count from the topic, or return default."""
    m = _COUNT_RE.search(topic)
    if m:
        val = m.group(1).lower()
        return _NUM_WORDS.get(val, int(val) if val.isdigit() else _DEFAULT_COUNT)
    return _DEFAULT_COUNT


def _extract_source(topic: str) -> tuple[str, str | None]:
    """Extract --from/--source flag and return (clean_topic, source_key or None)."""
    m = _SOURCE_RE.search(topic)
    if not m:
        return topic, None
    raw = m.group(1).lower()
    clean_topic = _SOURCE_RE.sub("", topic).strip()
    # Resolve alias to internal source key
    source_key = SOURCE_ALIASES.get(raw)
    return clean_topic, source_key


async def news(state: dict) -> dict:
    topic = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not topic:
        sources_list = ", ".join(sorted(SOURCE_ALIASES.keys()))
        return {"reply_text": (
            "Usage: /news <topic> [--from <source>]\n\n"
            f"Sources: {sources_list}"
        )}

    topic, source_override = _extract_source(topic)
    count = _extract_count(topic)
    fetch_per_source = max(3, (count + 2) // 2)

    if source_override:
        log.info("[news] Searching for: %s (source=%s, count=%d)", topic, source_override, count)
        results = await news_search_aggregated(
            topic, max_per_source=fetch_per_source * 2,
            sources_override=[source_override],
        )
    else:
        log.info("[news] Searching for: %s (count=%d, per_source=%d)", topic, count, fetch_per_source)
        results = await news_search_aggregated(topic, max_per_source=fetch_per_source)

    from datetime import date
    system = _NEWS_SYSTEM.replace("{date}", date.today().isoformat()).replace("{count}", str(count))

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"{topic}\n\n"
                f"News results:\n{results}"
            ),
        },
    ]
    reply = await chat_completion(messages, max_tokens=MAX_TOKENS_TOOL_ANSWER, temperature=TEMP_TOOL_ANSWER, no_think=True)
    return {"reply_text": reply}
