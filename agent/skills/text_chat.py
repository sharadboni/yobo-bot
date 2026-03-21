"""Text chat skill - classify then route to fast model or tool-calling model."""
from __future__ import annotations
import logging
from agent.services.llm import chat_completion_with_tools, chat_completion_fast
from agent.config import SYSTEM_PROMPT, MAX_HISTORY
from agent.tools import TOOLS, TOOL_EXECUTORS

log = logging.getLogger(__name__)

CONTEXT_TURNS = min(20, MAX_HISTORY)

_CLASSIFY_PROMPT = (
    "You are a classifier. Given a user message, decide if answering it requires "
    "searching the internet for CURRENT or REAL-TIME information (weather, news, "
    "prices, live scores, recent events).\n\n"
    "If the question can be answered from general knowledge (explanations, jokes, "
    "math, coding, opinions, greetings), output: no\n"
    "If it needs CURRENT data from the internet, output: yes\n\n"
    "Output ONLY one word: yes or no"
)


async def _needs_tools(text: str) -> bool:
    """Ask the fast model if the query needs external data. ~1s."""
    try:
        reply = await chat_completion_fast([
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": text},
        ], max_tokens=1, temperature=0)
        result = reply.strip().lower().startswith("yes")
        log.info("Classifier: %r -> needs_tools=%s", text[:50], result)
        return result
    except Exception as e:
        log.warning("Classifier failed, defaulting to no tools: %s", e)
        return False


def build_llm_messages(profile: dict, resolved_text: str) -> list[dict]:
    """Build the LLM message list from profile history + current input."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for entry in profile.get("history", [])[-CONTEXT_TURNS:]:
        messages.append({"role": entry["role"], "content": entry["content"]})
    messages.append({"role": "user", "content": resolved_text})
    return messages


async def text_chat(state: dict) -> dict:
    profile = state.get("user_profile", {})
    resolved = state.get("resolved_text", "")
    messages = build_llm_messages(profile, resolved)

    needs = await _needs_tools(resolved)

    if needs:
        log.info("Routing to tool-calling model for: %s", resolved[:60])
        try:
            reply = await chat_completion_with_tools(
                messages, tools=TOOLS, tool_executor=TOOL_EXECUTORS,
                max_rounds=5,
            )
        except Exception as e:
            log.warning("Tool calling failed, falling back to fast: %s", e)
            reply = await chat_completion_fast(messages)
    else:
        log.info("Routing to fast model for: %s", resolved[:60])
        reply = await chat_completion_fast(messages)

    return {"reply_text": reply}
