"""Text chat skill - conversational AI with tool calling."""
from __future__ import annotations
from agent.services.llm import chat_completion_with_tools, chat_completion
from agent.config import SYSTEM_PROMPT
from agent.tools import TOOLS, TOOL_EXECUTORS


async def text_chat(state: dict) -> dict:
    profile = state.get("user_profile", {})
    history = profile.get("history", [])
    resolved = state.get("resolved_text", "")

    # Build messages for LLM
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add conversation history
    for entry in history[-20:]:  # last 20 turns for context window
        messages.append({
            "role": entry["role"],
            "content": entry["content"],
        })

    # Current message
    messages.append({"role": "user", "content": resolved})

    try:
        reply = await chat_completion_with_tools(
            messages, tools=TOOLS, tool_executor=TOOL_EXECUTORS,
            max_rounds=5,  # search → read 1 → read 2 → read 3 → answer
        )
    except Exception:
        # Fallback to plain chat if tool calling not supported by provider
        reply = await chat_completion(messages)

    return {"reply_text": reply}
