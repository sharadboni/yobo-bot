"""Web search skill - explicit /search command."""
from __future__ import annotations
from agent.services.llm import chat_completion
from agent.config import get_system_prompt_tools
from agent.tools import web_search as _web_search


async def web_search(state: dict) -> dict:
    query = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not query:
        return {"reply_text": "Please provide a search query. Usage: /search <query>"}

    results = await _web_search(query)

    if results.startswith("All search providers failed"):
        return {"reply_text": "Search failed. Please try again later."}

    messages = [
        {"role": "system", "content": get_system_prompt_tools()},
        {
            "role": "user",
            "content": (
                f"The user searched for: {query}\n\n"
                f"Here are the search results:\n\n{results}\n\n"
                "Provide a helpful, concise summary based on these results. "
                "Include relevant links."
            ),
        },
    ]

    reply = await chat_completion(messages, no_think=True)
    return {"reply_text": reply}
