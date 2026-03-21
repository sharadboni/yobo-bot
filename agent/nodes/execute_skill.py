"""Execute the matched skill."""
from __future__ import annotations
import logging
from agent.skills.text_chat import text_chat
from agent.skills.web_search import web_search
from agent.skills.podcast import podcast
from agent.skills.schedule import schedule_add, schedule_list, schedule_remove
from agent.skills.voice import voice_cmd

log = logging.getLogger(__name__)

SKILLS = {
    "text_chat": text_chat,
    "web_search": web_search,
    "podcast": podcast,
    "schedule_add": schedule_add,
    "schedule_list": schedule_list,
    "schedule_remove": schedule_remove,
    "voice": voice_cmd,
}


async def execute_skill_node(state: dict) -> dict:
    intent = state.get("intent", "text_chat")

    # Short-circuit for non-skill intents
    if intent.startswith("__"):
        return {}

    if intent == "help":
        return {
            "reply_text": (
                "Available commands:\n"
                "/search <query> - Search the web\n"
                "/s <query> - Search shortcut\n"
                "/podcast <topic> - Generate a podcast on a topic\n"
                "/p <topic> - Podcast shortcut\n"
                "/schedule <type> <freq> [day] <time> [--audio] <topic> - Schedule a task\n"
                "/schedules - List your scheduled tasks\n"
                "/unschedule <id> - Remove a scheduled task\n"
                "/voice - Manage TTS voice (set, add custom, list)\n"
                "/help - Show this message\n\n"
                "Or just send a message to chat!"
            )
        }

    skill_fn = SKILLS.get(intent, text_chat)
    try:
        result = await skill_fn(state)
        return result
    except Exception as e:
        log.error("Skill %s failed: %s", intent, e, exc_info=True)
        return {
            "reply_text": "Sorry, something went wrong. Please try again.",
            "intent": "__error__",
        }
