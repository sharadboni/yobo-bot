"""News skill — fetch and summarize news from multiple sources."""
from __future__ import annotations
import logging
from agent.tools import news_search
from agent.services.llm import chat_completion
from agent.config import get_system_prompt_tools
from agent.constants import MAX_TOKENS_TOOL_ANSWER, TEMP_TOOL_ANSWER

log = logging.getLogger(__name__)


async def news(state: dict) -> dict:
    topic = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not topic:
        return {"reply_text": "Please provide a topic. Usage: /news <topic>"}

    log.info("[news] Searching for: %s", topic)
    results = await news_search(topic)

    messages = [
        {"role": "system", "content": get_system_prompt_tools()},
        {
            "role": "user",
            "content": (
                f"Give me a detailed news briefing about: {topic}\n\n"
                f"News results:\n{results}\n\n"
                "Summarize each key story. Merge duplicates. Cover diverse topics. "
                "Give complete answers with 8-10 stories."
            ),
        },
    ]
    reply = await chat_completion(messages, max_tokens=MAX_TOKENS_TOOL_ANSWER, temperature=TEMP_TOOL_ANSWER, no_think=True)
    return {"reply_text": reply}
