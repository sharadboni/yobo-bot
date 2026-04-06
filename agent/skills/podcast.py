"""Podcast skill — research a topic and generate a voice-note podcast."""
from __future__ import annotations
import logging
from agent.tools import news_search_aggregated, web_search, read_page, wikipedia
from agent.services.llm import chat_completion
from agent.services.voice_store import get_dialogue_voices
from agent.constants import MAX_TOKENS_PODCAST_SCRIPT, MAX_WORDS_PODCAST_DIALOGUE, TEMP_PODCAST_SCRIPT

log = logging.getLogger(__name__)

PODCAST_SCRIPT_PROMPT = """\
You are a podcast host speaking directly to a listener. Based on the research \
material below, deliver a natural spoken monologue about the topic.

Rules:
- Write EXACTLY as you would speak out loud.
- Use expressive punctuation for emotional delivery: ellipses for pauses..., \
dashes for interruptions—, exclamation marks for emphasis!, question marks for \
rhetorical questions?
- Use contractions, rhetorical questions, and natural transitions.
- STRICT LENGTH: Target 450-500 words. Plan your content to fit.
- Structure: Hook opening → 3-4 key points → Strong closing takeaway.
- You MUST end with a complete closing statement that wraps up the episode.
- FORBIDDEN: No asterisks, no bold, no bullet points, no headers, no markdown, \
no brackets, no parenthetical notes, no speaker labels, no stage directions.
- Output ONLY the spoken words. Nothing else before or after.
"""

def _build_dialogue_prompt(host_name: str, guest_name: str, host_gender: str, guest_gender: str) -> str:
    """Build a dialogue script prompt with character names and dynamic pairing."""
    # Describe the pairing
    if host_gender == guest_gender == "female":
        dynamic = f"{host_name} and {guest_name} are two sharp women with great chemistry."
    elif host_gender == guest_gender == "male":
        dynamic = f"{host_name} and {guest_name} are two guys who riff off each other naturally."
    elif host_gender == "female" and guest_gender == "male":
        dynamic = f"{host_name} leads the conversation with curiosity, {guest_name} brings depth and analysis."
    elif host_gender == "male" and guest_gender == "female":
        dynamic = f"{host_name} sets up the topics, {guest_name} brings sharp insights and pushback."
    else:
        dynamic = f"{host_name} drives the conversation, {guest_name} provides expert commentary."

    return (
        f"You are writing a podcast dialogue between {host_name} (HOST) and {guest_name} (GUEST). "
        f"{dynamic}\n\n"
        "Rules:\n"
        f"- Each line must start with exactly \"HOST:\" or \"GUEST:\" followed by spoken words.\n"
        f"- {host_name} (HOST) drives with questions and transitions. "
        f"{guest_name} (GUEST) provides insights and interesting takes.\n"
        "- Use expressive punctuation for emotional delivery: ellipses for pauses..., "
        "dashes for interruptions—, exclamation marks for emphasis!, commas for breaths.\n"
        "- Use contractions, reactions (\"Right!\", \"Exactly!\", \"That's wild—\"), "
        "laughter cues (\"Ha!\"), and natural back-and-forth.\n"
        f"- STRICT LENGTH: Target {MAX_WORDS_DIALOGUE - 50}-{MAX_WORDS_DIALOGUE} words total. Plan content to fit.\n"
        f"- Structure: HOST introduces topic → 3-4 discussion points → HOST wraps up with a closing line.\n"
        "- You MUST end with HOST delivering a complete sign-off that wraps up the conversation.\n"
        "- FORBIDDEN: No asterisks, no bold, no bullet points, no headers, no markdown, "
        "no brackets, no parenthetical notes, no stage directions.\n"
        "- Output ONLY the dialogue lines. Nothing else before or after."
    )

MAX_WORDS_DIALOGUE = MAX_WORDS_PODCAST_DIALOGUE
MAX_PAGES_TO_READ = 3

# --- VibeVoice dialogue voice helpers ---

def _get_character_name(voice_name: str) -> str:
    """Extract character name from a VibeVoice voice (e.g. 'en-Emma_woman' -> 'Emma')."""
    if "-" in voice_name and "_" in voice_name:
        # VibeVoice format: lang-Name_gender
        _, rest = voice_name.split("-", 1)
        name = rest.rsplit("_", 1)[0]
        return name
    # Kokoro or custom — use voice name capitalized
    return voice_name.replace("_", " ").title()


def _get_voice_gender(voice_name: str) -> str:
    """Detect gender from voice name."""
    # VibeVoice: en-Emma_woman, en-Carter_man
    if voice_name.endswith("_woman"):
        return "female"
    if voice_name.endswith("_man"):
        return "male"
    # Kokoro fallback: xf_ = female, xm_ = male
    if len(voice_name) >= 2 and voice_name[1] == "f":
        return "female"
    if len(voice_name) >= 2 and voice_name[1] == "m":
        return "male"
    return "unknown"



def _parse_dialogue(script: str, host_voice: str, guest_voice: str,
                    clone_voice: dict | None = None) -> list[dict]:
    """Parse HOST:/GUEST: labelled script into segments with voice info.

    host_voice / guest_voice are VibeVoice names for preset dialogue.
    clone_voice, if provided, overrides host segments with voice cloning data.
    """
    segments = []
    for line in script.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("HOST:"):
            seg = {"voice": host_voice, "text": line[5:].strip()}
            if clone_voice and clone_voice.get("ref_audio_b64"):
                seg["ref_audio"] = clone_voice["ref_audio_b64"]
                if clone_voice.get("ref_text"):
                    seg["ref_text"] = clone_voice["ref_text"]
            segments.append(seg)
        elif line.startswith("GUEST:"):
            segments.append({"voice": guest_voice, "text": line[6:].strip()})
        else:
            # Continuation of previous speaker — append to last segment
            if segments:
                segments[-1]["text"] += " " + line
            else:
                seg = {"voice": host_voice, "text": line}
                if clone_voice and clone_voice.get("ref_audio_b64"):
                    seg["ref_audio"] = clone_voice["ref_audio_b64"]
                    if clone_voice.get("ref_text"):
                        seg["ref_text"] = clone_voice["ref_text"]
                segments.append(seg)
    return segments


async def _research(topic: str) -> str | None:
    """Gather research from news, Wikipedia, and web search concurrently."""
    import asyncio
    log.info("[podcast] Researching: %s", topic)

    # Fetch news, Wikipedia, and web search in parallel
    async def _safe(name, coro):
        try:
            result = await coro
            if result and not result.startswith("All search providers failed"):
                log.info("[podcast] %s returned %d chars", name, len(result))
                return result
        except Exception as e:
            log.warning("[podcast] %s failed: %s", name, e)
        return ""

    news_result, wiki_result, web_result = await asyncio.gather(
        _safe("news", news_search_aggregated(topic)),
        _safe("wikipedia", wikipedia(topic)),
        _safe("web_search", web_search(topic)),
    )

    if not news_result and not wiki_result and not web_result:
        return None

    # Read top pages from news + web results for deeper content
    all_text = (news_result or "") + "\n" + (web_result or "")
    urls = [line.strip() for line in all_text.split("\n") if line.strip().startswith("http")]
    # Deduplicate URLs
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    page_contents = []
    for url in unique_urls[:MAX_PAGES_TO_READ]:
        log.info("[podcast] Reading: %s", url)
        content = await read_page(url)
        if content and not content.startswith("Failed") and not content.startswith("Cannot"):
            page_contents.append(content[:2000])

    # Build research document
    research = ""
    if news_result:
        research += f"NEWS RESULTS:\n{news_result}\n\n"
    if wiki_result:
        research += f"BACKGROUND (Wikipedia):\n{wiki_result[:3000]}\n\n"
    if web_result:
        research += f"WEB SEARCH:\n{web_result}\n\n"
    if page_contents:
        research += "DETAILED CONTENT:\n\n"
        for i, content in enumerate(page_contents, 1):
            research += f"--- Source {i} ---\n{content}\n\n"
    return research


async def _generate_script(
    system_prompt: str,
    topic: str,
    research: str,
) -> str:
    """Generate a podcast script. No condensation — the prompt controls length."""
    log.info("[podcast] Generating script...")
    script = await chat_completion([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Topic: {topic}\n\n{research}\n\nSpeak now."},
    ], max_tokens=MAX_TOKENS_PODCAST_SCRIPT, temperature=TEMP_PODCAST_SCRIPT, no_think=True)

    word_count = len(script.split())
    log.info("[podcast] Script ready: %d words, %d chars", word_count, len(script))
    return script


async def podcast(state: dict) -> dict:
    topic = state.get("intent_args", "").strip() or state.get("resolved_text", "")
    if not topic:
        return {"reply_text": "Please provide a topic. Usage: /podcast <topic>"}

    # Check for --dialogue flag
    dialogue_mode = False
    if "--dialogue" in topic or "--duo" in topic:
        dialogue_mode = True
        topic = topic.replace("--dialogue", "").replace("--duo", "").strip()

    research = await _research(topic)
    if research is None:
        return {"reply_text": "Couldn't find information on that topic. Try again later."}

    if dialogue_mode:
        user_jid = state.get("user_jid", "")
        # Get user's duo voice settings (host can be custom/cloned)
        duo = get_dialogue_voices(user_jid)
        host_voice = duo["host"]   # {"name", "ref_audio_b64", "ref_text"}
        guest_voice = duo["guest"]

        host_vv = host_voice["name"]
        guest_vv = guest_voice["name"]
        host_name = _get_character_name(host_vv)
        guest_name = _get_character_name(guest_vv)
        host_gender = _get_voice_gender(host_vv)
        guest_gender = _get_voice_gender(guest_vv)
        # Custom/cloned host voice → pass ref_audio for voice cloning segments
        clone_voice = host_voice if host_voice.get("ref_audio_b64") else None
        log.info("[podcast] Dialogue: host=%s guest=%s cloned=%s",
                 host_vv, guest_vv, bool(clone_voice))

        dialogue_prompt = _build_dialogue_prompt(host_name, guest_name, host_gender, guest_gender)
        script = await _generate_script(dialogue_prompt, topic, research)
        segments = _parse_dialogue(script, host_vv, guest_vv, clone_voice)
        if not segments:
            return {"reply_text": script, "content_type": "audio"}
        # Replace HOST:/GUEST: labels with friendly names for display
        display_script = script.replace("HOST:", f"{host_name}:").replace("GUEST:", f"{guest_name}:")
        return {
            "reply_text": display_script,
            "content_type": "dialogue",
            "dialogue_segments": segments,
        }
    else:
        script = await _generate_script(PODCAST_SCRIPT_PROMPT, topic, research)
        return {"reply_text": script, "content_type": "audio"}
