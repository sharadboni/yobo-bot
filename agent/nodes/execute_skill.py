"""Execute the matched skill."""
from __future__ import annotations
import logging
from agent.skills.text_chat import text_chat
from agent.skills.news import news as news_skill
from agent.skills.web_search import web_search
from agent.skills.podcast import podcast
from agent.skills.schedule import schedule_add, schedule_list, schedule_remove
from agent.skills.voice import voice_cmd
from agent.skills.say import say

log = logging.getLogger(__name__)

SKILLS = {
    "text_chat": text_chat,
    "news": news_skill,
    "web_search": web_search,
    "podcast": podcast,
    "schedule_add": schedule_add,
    "schedule_list": schedule_list,
    "schedule_remove": schedule_remove,
    "voice": voice_cmd,
    "say": say,
}


async def execute_skill_node(state: dict) -> dict:
    intent = state.get("intent", "text_chat")

    # Short-circuit for non-skill intents
    if intent.startswith("__"):
        return {}

    if intent == "help":
        return {
            "reply_text": (
                "Hey! I'm Yobo, your AI assistant. Here's what I can do:\n\n"

                "Just chat with me naturally — I can answer questions, search the web, look up news, and more. "
                "You can also send me voice notes, photos, or documents and I'll understand them.\n\n"

                "---\n\n"

                "*Chat & Search*\n"
                "/news AI technology — get a news briefing\n"
                "/search weather in Boston\n"
                "/s latest iPhone price\n\n"

                "*Podcasts*\n"
                "/podcast AI breakthroughs\n"
                "/podcast space exploration --dialogue\n"
                "The --dialogue flag creates a two-voice conversation!\n\n"

                "*Scheduled Updates*\n"
                "/schedule news daily 8am AI technology\n"
                "/schedule podcast weekly monday 9am tech --audio\n"
                "/schedules to see yours, /unschedule <id> to remove\n\n"

                "*Voice & TTS*\n"
                "/say Hello world — convert text to speech\n"
                "/voice list — browse 50+ voices\n"
                "/voice set af bella — change your voice\n"
                "/voice add myvoice Hello this is my voice\n"
                "Then send a voice note to clone your voice!\n\n"

                "*Documents*\n"
                "Send any PDF, TXT, CSV, or JSON file.\n"
                "Add a caption like \"Summarize this\" or \"Find the key takeaways\"\n\n"

                "---\n\n"

                "Tip: Send me a photo with a caption to ask about it, "
                "or just say hi to get started!"
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
