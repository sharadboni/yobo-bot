"""Text chat skill - classify then route to fast model or tool-calling model."""
from __future__ import annotations
import logging
from agent.services.llm import chat_completion, chat_completion_with_tools, chat_completion_fast
from agent.config import get_system_prompt_fast, get_system_prompt_tools, get_system_prompt_document, MAX_HISTORY
from agent.constants import MAX_TOKENS_CLASSIFIER, TEMP_CLASSIFIER
from agent.tools import TOOLS, TOOL_EXECUTORS

log = logging.getLogger(__name__)

CONTEXT_TURNS = min(20, MAX_HISTORY)

_CLASSIFY_PROMPT = (
    "Decide if this message needs a data lookup (news, weather, prices, "
    "companies, facts, events) or can be answered from general knowledge "
    "(greetings, math, coding, opinions, creative writing).\n"
    "When in doubt, output YES.\n"
    "Output ONLY: yes or no"
)


async def _needs_tools(text: str) -> bool:
    """Ask the fast model if the query needs external data. ~1s."""
    try:
        reply = await chat_completion_fast([
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": text},
        ], max_tokens=MAX_TOKENS_CLASSIFIER, temperature=TEMP_CLASSIFIER)
        result = reply.strip().lower().startswith("yes")
        log.info("Classifier: %r -> needs_tools=%s", text[:50], result)
        return result
    except Exception as e:
        log.warning("Classifier failed, defaulting to tools: %s", e)
        return True  # safer to use tools than to hallucinate


def _build_messages(system_prompt: str, profile: dict, resolved_text: str, include_history: bool = True) -> list[dict]:
    """Build the LLM message list from profile history + current input.

    History entries with metadata get the meta injected as context
    so the LLM can reference sources/URLs for follow-ups.
    """
    messages = [{"role": "system", "content": system_prompt}]
    if include_history:
        for entry in profile.get("history", [])[-CONTEXT_TURNS:]:
            content = entry["content"]
            meta = entry.get("meta")
            if meta:
                # Inject metadata as hidden context the LLM can reference
                parts = [content]
                if meta.get("sources"):
                    parts.append(f"\n[Sources: {', '.join(meta['sources'])}]")
                if meta.get("urls"):
                    url_list = "\n".join(f"- {u}" for u in meta["urls"])
                    parts.append(f"\n[Reference URLs:\n{url_list}]")
                content = "".join(parts)
            messages.append({"role": entry["role"], "content": content})
    messages.append({"role": "user", "content": resolved_text})
    return messages


# Keep for external use (e.g. task_handlers)
def build_llm_messages(profile: dict, resolved_text: str) -> list[dict]:
    return _build_messages(get_system_prompt_tools(), profile, resolved_text)


async def text_chat(state: dict) -> dict:
    profile = state.get("user_profile", {})
    resolved = state.get("resolved_text", "")

    # Document inputs: skip classifier, use document prompt
    if "[TOOL RESULT from document:" in resolved:
        log.info("Routing document to text model (no tools): %s", resolved[:60])
        messages = _build_messages(get_system_prompt_document(), profile, resolved)
        reply = await chat_completion(messages, no_think=True)
        return {"reply_text": reply}

    needs = await _needs_tools(resolved)

    if needs:
        log.info("Routing to tool-calling model for: %s", resolved[:60])
        messages = _build_messages(get_system_prompt_tools(), profile, resolved)
        try:
            reply = await chat_completion_with_tools(
                messages, tools=TOOLS, tool_executor=TOOL_EXECUTORS,
                max_rounds=5,
            )
        except Exception as e:
            log.warning("Tool calling failed, falling back to fast: %s", e)
            messages = _build_messages(get_system_prompt_fast(), profile, resolved)
            reply = await chat_completion_fast(messages)
    else:
        log.info("Routing to fast model for: %s", resolved[:60])
        messages = _build_messages(get_system_prompt_fast(), profile, resolved)
        reply = await chat_completion_fast(messages)

    return {"reply_text": reply}
