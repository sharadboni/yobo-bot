"""Text chat skill - conversational AI with tool calling."""
from __future__ import annotations
import logging
from agent.services.llm import chat_completion_with_tools, chat_completion
from agent.config import SYSTEM_PROMPT, MAX_HISTORY
from agent.tools import TOOLS, TOOL_EXECUTORS

log = logging.getLogger(__name__)

# How many history entries to include in LLM context
CONTEXT_TURNS = min(20, MAX_HISTORY)


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

    # Try tool-calling first, fall back to plain chat only on tool-related errors
    try:
        reply = await chat_completion_with_tools(
            messages, tools=TOOLS, tool_executor=TOOL_EXECUTORS,
            max_rounds=5,
        )
    except RuntimeError:
        # All providers failed — try plain chat as last resort
        reply = await chat_completion(messages)
    except Exception as e:
        # Tool calling format not supported by this provider
        log.info("Tool calling unavailable, falling back to plain chat: %s", e)
        reply = await chat_completion(messages)

    return {"reply_text": reply}
