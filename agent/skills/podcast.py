"""Podcast skill — research a topic and generate a voice-note podcast."""
from __future__ import annotations
import logging
from agent.tools import news_search_aggregated, web_search, read_page, wikipedia
from agent.services.llm import chat_completion
from agent.services.voice_store import get_active_voice
from agent.constants import MAX_TOKENS_PODCAST_SCRIPT, MAX_WORDS_PODCAST_MONO, MAX_WORDS_PODCAST_DIALOGUE

log = logging.getLogger(__name__)

PODCAST_SCRIPT_PROMPT = """\
You are a podcast host speaking directly to a listener. Based on the research \
material below, deliver a natural spoken monologue about the topic.

Rules:
- Write EXACTLY as you would speak out loud. No written formatting at all.
- Use contractions, rhetorical questions, pauses, and natural transitions.
- STRICT LENGTH: Maximum 500 words. This is absolutely critical.
- Start with a hook, cover key points, end with a takeaway.
- FORBIDDEN: No asterisks, no bold, no bullet points, no headers, no markdown, \
no brackets, no parenthetical notes, no speaker labels, no stage directions.
- Output ONLY the spoken words. Nothing else before or after.
"""

DIALOGUE_SCRIPT_PROMPT = """\
You are writing a short 2-person podcast dialogue between HOST and GUEST about the \
topic below, based on the research material provided.

Rules:
- Write natural, conversational dialogue. Each line must start with exactly \
"HOST:" or "GUEST:" followed by their spoken words.
- HOST drives the conversation with questions and transitions. \
GUEST provides insights and interesting takes.
- Use contractions, reactions ("Right!", "Exactly", "That's wild"), and natural back-and-forth.
- STRICT LENGTH: Maximum 700 words total. This is absolutely critical.
- Start with HOST introducing the topic, end with a quick wrap-up.
- FORBIDDEN: No asterisks, no bold, no bullet points, no headers, no markdown, \
no brackets, no parenthetical notes, no stage directions.
- Output ONLY the dialogue lines. Nothing else before or after.
"""

CONDENSE_PROMPT = """\
The following podcast script is too long. Condense it to under {max_words} words while \
keeping it natural and conversational. Keep the hook and takeaway. \
Do not add any formatting — output ONLY the spoken words."""

CONDENSE_DIALOGUE_PROMPT = """\
The following podcast dialogue is too long. Condense it to under {max_words} words while \
keeping it natural and conversational. Preserve the HOST:/GUEST: labels on each line. \
Do not add any formatting — output ONLY the dialogue lines."""

MAX_WORDS_MONO = MAX_WORDS_PODCAST_MONO
MAX_WORDS_DIALOGUE = MAX_WORDS_PODCAST_DIALOGUE
MAX_RETRIES = 2
MAX_PAGES_TO_READ = 3

# Contrasting voice pairs: if user has a female voice, guest is male, and vice versa
_CONTRAST_VOICES = {
    # American English
    "af_heart": "am_fenrir", "af_bella": "am_fenrir", "af_nova": "am_puck", "af_sky": "bm_george",
    "am_adam": "af_heart", "am_echo": "af_bella", "am_eric": "af_nova", "am_liam": "af_heart",
    "am_michael": "af_heart", "am_fenrir": "af_bella", "am_puck": "af_nova",
    # British English
    "bf_alice": "bm_george", "bf_emma": "bm_fable",
    "bm_daniel": "bf_alice", "bm_fable": "bf_emma", "bm_george": "bf_alice", "bm_lewis": "bf_emma",
    # Spanish
    "ef_dora": "em_alex", "em_alex": "ef_dora", "em_santa": "ef_dora",
    # Hindi
    "hf_alpha": "hm_omega", "hf_beta": "hm_psi", "hm_omega": "hf_alpha", "hm_psi": "hf_beta",
}
_DEFAULT_GUEST = "am_fenrir"


def _pick_guest_voice(host_voice_name: str) -> str:
    """Pick a contrasting guest voice based on the host voice."""
    if host_voice_name in _CONTRAST_VOICES:
        return _CONTRAST_VOICES[host_voice_name]
    # For custom/cloned voices or unknown builtins, guess from prefix
    if host_voice_name and len(host_voice_name) >= 2:
        gender = host_voice_name[1]  # 'f' or 'm' in the naming convention
        if gender == "f":
            return "am_adam"
        elif gender == "m":
            return "af_heart"
    return _DEFAULT_GUEST


def _parse_dialogue(script: str, host_voice: dict, guest_voice: str) -> list[dict]:
    """Parse HOST:/GUEST: labelled script into segments with voice info."""
    segments = []
    for line in script.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("HOST:"):
            seg = {"voice": host_voice["name"], "text": line[5:].strip()}
            if host_voice.get("ref_audio_b64"):
                seg["ref_audio"] = host_voice["ref_audio_b64"]
                if host_voice.get("ref_text"):
                    seg["ref_text"] = host_voice["ref_text"]
            segments.append(seg)
        elif line.startswith("GUEST:"):
            segments.append({"voice": guest_voice, "text": line[6:].strip()})
        else:
            # Continuation of previous speaker — append to last segment
            if segments:
                segments[-1]["text"] += " " + line
            else:
                seg = {"voice": host_voice["name"], "text": line}
                if host_voice.get("ref_audio_b64"):
                    seg["ref_audio"] = host_voice["ref_audio_b64"]
                    if host_voice.get("ref_text"):
                        seg["ref_text"] = host_voice["ref_text"]
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


async def _generate_and_condense(
    system_prompt: str,
    topic: str,
    research: str,
    max_words: int,
    condense_prompt_tmpl: str,
) -> str:
    """Generate a script and condense if too long."""
    log.info("[podcast] Generating script...")
    script = await chat_completion([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Topic: {topic}\n\n{research}\n\nSpeak now."},
    ], max_tokens=MAX_TOKENS_PODCAST_SCRIPT, no_think=True)

    word_count = len(script.split())
    for attempt in range(MAX_RETRIES):
        if word_count <= max_words:
            break
        log.info("[podcast] Script too long (%d words), condensing (attempt %d)...", word_count, attempt + 1)
        script = await chat_completion([
            {"role": "system", "content": condense_prompt_tmpl.format(max_words=max_words)},
            {"role": "user", "content": script},
        ], max_tokens=MAX_TOKENS_PODCAST_SCRIPT, no_think=True)
        word_count = len(script.split())

    if word_count > max_words:
        log.warning("[podcast] Script still %d words after %d condense attempts", word_count, MAX_RETRIES)

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
        script = await _generate_and_condense(
            DIALOGUE_SCRIPT_PROMPT, topic, research,
            MAX_WORDS_DIALOGUE, CONDENSE_DIALOGUE_PROMPT,
        )
        user_jid = state.get("user_jid", "")
        host_voice = get_active_voice(user_jid)
        guest_voice = _pick_guest_voice(host_voice["name"])
        log.info("[podcast] Dialogue voices: host=%s guest=%s", host_voice["name"], guest_voice)
        segments = _parse_dialogue(script, host_voice, guest_voice)
        if not segments:
            return {"reply_text": script, "content_type": "audio"}
        return {
            "reply_text": script,
            "content_type": "dialogue",
            "dialogue_segments": segments,
        }
    else:
        script = await _generate_and_condense(
            PODCAST_SCRIPT_PROMPT, topic, research,
            MAX_WORDS_MONO, CONDENSE_PROMPT,
        )
        return {"reply_text": script, "content_type": "audio"}
