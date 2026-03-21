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
                "*Available Commands:*\n\n"
                "*Chat & Search*\n"
                "/search <query> — Search the web\n"
                "/s <query> — Search shortcut\n\n"
                "*Podcast*\n"
                "/podcast <topic> — Generate a podcast voice note\n"
                "/p <topic> — Podcast shortcut\n\n"
                "*Scheduling*\n"
                "/schedule <type> <freq> [day] <time> [--audio] <topic>\n"
                "/schedules — List your scheduled tasks\n"
                "/unschedule <id> — Remove a scheduled task\n\n"
                "*Voice*\n"
                "/voice — Show current voice\n"
                "/voice list — List all voices\n"
                "/voice set <name> — Switch voice\n"
                "/voice add <name> <transcript> — Clone a voice\n"
                "/voice remove <name> — Remove a voice\n\n"
                "/help — Show this message\n\n"
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
