"""LLM service with per-capability fallback across endpoints."""
from __future__ import annotations
import logging
import re
import tempfile
import os
import openai
import httpx
from agent.config import LLM_CONFIG
from agent.constants import MAX_TOKENS_TOOL_ROUND, MAX_TOKENS_TOOL_ANSWER
from agent.sanitize import sanitize_tool_output, wrap_tool_result

log = logging.getLogger(__name__)


def _get_providers(capability: str) -> list[dict]:
    providers = LLM_CONFIG.get(capability, [])
    if not providers:
        raise RuntimeError(f"No providers configured for '{capability}' in llm_config.yaml")
    return providers


def _strip_think(text: str) -> str:
    """Strip <think>...</think> blocks and stray tags from model output."""
    # Complete <think>...</think> blocks
    result = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
    # Incomplete <think> block (truncated) — remove from <think> to end
    result = re.sub(r"<think>[\s\S]*$", "", result)
    # Stray closing tags (e.g. model outputs just "</think>" at the start)
    result = re.sub(r"</think>\s*", "", result)
    # Variations: <|think|>, etc.
    result = re.sub(r"<\|?/?think\|?>\s*", "", result)
    return result.strip() or text.strip()


def _extract_content(choice) -> str:
    """Extract text from a completion choice, handling reasoning_content models.

    Qwen3 models on exo put everything in reasoning_content with content="".
    The actual answer is typically after the reasoning, often following markers
    like 'Final Response:', 'Final Answer:', or after the last numbered step.
    """
    content = choice.message.content or ""
    stripped = _strip_think(content)
    if stripped:
        return stripped

    # Fallback: extract from reasoning_content
    reasoning = getattr(choice.message, "reasoning_content", "") or ""
    if not reasoning:
        return content.strip()

    # Try to find the final answer after common markers
    for marker in [
        "Final Response:", "Final Answer:", "Final Output:",
        "Response:", "Answer:", "Output:",
        "Here is the", "Here's the",
    ]:
        idx = reasoning.rfind(marker)
        if idx != -1:
            answer = reasoning[idx + len(marker):].strip()
            if answer:
                return _strip_think(answer)

    # No marker found — use the full reasoning with think tags stripped
    return _strip_think(reasoning)


_THINK_PREFILL = {"role": "assistant", "content": "<think>\n\n</think>\n", "prefix": True}


def _thinking_kwargs(p: dict, no_think: bool) -> dict:
    """Build extra kwargs for thinking mode control.

    mlx-omni-server: use native thinking param. Only enable for tool calling
    (no_think=False), disable for everything else.
    Other providers: use the <think></think> prefill hack.
    """
    if p.get("type") == "mlx_omni" or "8765" in p.get("base_url", ""):
        return {"extra_body": {"thinking": False}, "use_prefill": False}
    return {"extra_body": {}, "use_prefill": no_think}


def _thinking_kwargs_tools(p: dict) -> dict:
    """Thinking kwargs for tool-calling requests.
    Thinking OFF — thinking=true causes timeouts on local 9B models.
    The system prompt instructs the model to always use tools for current data.
    """
    if p.get("type") == "mlx_omni" or "8765" in p.get("base_url", ""):
        return {"extra_body": {"thinking": False}}
    return {"extra_body": {}}


async def chat_completion(messages: list[dict], **overrides) -> str:
    """Text completion with fallback.

    Set no_think=True to disable thinking mode.
    """
    no_think = overrides.pop("no_think", False)
    last_err = None
    for p in _get_providers("text"):
        try:
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=180.0)
            msgs = list(messages)
            tk = _thinking_kwargs(p, no_think)
            if tk["use_prefill"]:
                msgs.append(_THINK_PREFILL)
            resp = await client.chat.completions.create(
                model=p["model"],
                messages=msgs,
                max_tokens=overrides.get("max_tokens", p["max_tokens"]),
                temperature=overrides.get("temperature", p["temperature"]),
                extra_body=tk["extra_body"] or openai.NOT_GIVEN,
            )
            return _extract_content(resp.choices[0])
        except Exception as e:
            log.warning("[text] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All text providers failed. Last: {last_err}")


async def chat_completion_fast(messages: list[dict], **overrides) -> str:
    """Fast text completion using the text_fast model (smaller, capped tokens).
    Always uses no_think. Falls back to regular text providers.
    """
    last_err = None
    providers = LLM_CONFIG.get("text_fast", []) or _get_providers("text")
    for p in providers:
        try:
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=60.0)
            msgs = list(messages)
            tk = _thinking_kwargs(p, True)  # fast model always no_think
            if tk["use_prefill"]:
                msgs.append(_THINK_PREFILL)
            resp = await client.chat.completions.create(
                model=p["model"],
                messages=msgs,
                max_tokens=overrides.get("max_tokens", p.get("max_tokens", 256)),
                temperature=overrides.get("temperature", p.get("temperature", 0.5)),
                extra_body=tk["extra_body"] or openai.NOT_GIVEN,
            )
            return _extract_content(resp.choices[0])
        except Exception as e:
            log.warning("[text_fast] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All text_fast providers failed. Last: {last_err}")


async def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    tool_executor: dict,
    max_rounds: int = 3,
    **overrides,
) -> str:
    """Chat completion with tool calling loop.

    Args:
        messages: Chat messages.
        tools: OpenAI-format tool definitions.
        tool_executor: Map of tool name → async callable(arguments) → str.
        max_rounds: Max tool call rounds before forcing a final answer.
    """
    last_err = None
    for p in _get_providers("text"):
        try:
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=180.0)
            msgs = list(messages)
            tk = _thinking_kwargs_tools(p)

            # Cap max_tokens for tool-calling rounds to prevent runaway thinking.
            # The model only needs to decide which tool to call (~500 tokens),
            # not generate a full response. Final answer round uses full max_tokens.
            tool_round_max_tokens = min(overrides.get("max_tokens", p["max_tokens"]), MAX_TOKENS_TOOL_ROUND)

            for round_num in range(max_rounds):
                extra = tk["extra_body"] or openai.NOT_GIVEN
                log.info("[tools] round=%d provider=%s max_tokens=%d extra_body=%s tools=%d",
                         round_num, p["name"], tool_round_max_tokens, extra, len(tools))
                resp = await client.chat.completions.create(
                    model=p["model"],
                    messages=msgs,
                    tools=tools,
                    max_tokens=tool_round_max_tokens,
                    temperature=overrides.get("temperature", p["temperature"]),
                    extra_body=extra,
                )
                choice = resp.choices[0]

                # No tool calls — return the final text
                if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                    return _extract_content(choice)

                # Process tool calls
                msgs.append(choice.message)
                for tc in choice.message.tool_calls:
                    fn_name = tc.function.name
                    fn_args = tc.function.arguments
                    executor = tool_executor.get(fn_name)
                    if executor:
                        try:
                            import json as _json
                            args = _json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                            result = await executor(**args)
                        except Exception as e:
                            log.warning("Tool %s failed: %s", fn_name, e)
                            result = f"Tool error: {e}"
                    else:
                        result = f"Unknown tool: {fn_name}"
                    # Sanitize and wrap external content
                    safe_result = sanitize_tool_output(str(result), source=fn_name)
                    wrapped = wrap_tool_result(safe_result, fn_name)
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": wrapped,
                    })

            # Max rounds reached — get final answer without tools
            resp = await client.chat.completions.create(
                model=p["model"],
                messages=msgs,
                max_tokens=overrides.get("max_tokens", min(p["max_tokens"], MAX_TOKENS_TOOL_ANSWER)),
                temperature=overrides.get("temperature", p["temperature"]),
            )
            return _extract_content(resp.choices[0])
        except Exception as e:
            log.warning("[text+tools] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All text providers failed. Last: {last_err}")


async def vision_completion(image_b64: str, prompt: str, **overrides) -> str:
    """Vision completion with fallback. Supports both mlx_omni and openai providers."""
    last_err = None
    for p in _get_providers("vision"):
        ptype = p.get("type", "openai")
        try:
            if ptype == "mlx_omni":
                text = await _mlx_omni_vision(p, image_b64, prompt, **overrides)
            else:
                text = await _openai_vision(p, image_b64, prompt, **overrides)
            return text
        except Exception as e:
            log.warning("[vision] %s (%s) failed: %s", p["name"], ptype, e)
            last_err = e
    raise RuntimeError(f"All vision providers failed. Last: {last_err}")


async def _mlx_omni_vision(p: dict, image_b64: str, prompt: str, **overrides) -> str:
    """Call mlx-omni-server POST /v1/vision."""
    url = f"{p['base_url'].rstrip('/')}/vision"
    payload = {
        "image": image_b64,
        "prompt": prompt,
        "max_tokens": overrides.get("max_tokens", p.get("max_tokens", 2048)),
        "temperature": overrides.get("temperature", p.get("temperature", 0.7)),
    }
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()["text"]


async def _openai_vision(p: dict, image_b64: str, prompt: str, **overrides) -> str:
    """Call OpenAI-compatible chat completions with image."""
    client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=120.0)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    resp = await client.chat.completions.create(
        model=p["model"],
        messages=messages,
        max_tokens=overrides.get("max_tokens", p.get("max_tokens", 1024)),
        temperature=overrides.get("temperature", p.get("temperature", 0.7)),
    )
    return resp.choices[0].message.content


async def transcribe_audio(audio_bytes: bytes, mimetype: str = "audio/ogg") -> str:
    """Speech-to-text with fallback."""
    ext = mimetype.split("/")[-1].split(";")[0]
    last_err = None
    for p in _get_providers("stt"):
        try:
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=120.0)
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            try:
                with open(tmp_path, "rb") as audio_file:
                    transcript = await client.audio.transcriptions.create(
                        model=p["model"],
                        file=audio_file,
                    )
                return transcript.text
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            log.warning("[stt] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All STT providers failed. Last: {last_err}")


async def synthesize_speech(
    text: str,
    voice_name: str | None = None,
    ref_audio_b64: str | None = None,
    ref_text: str | None = None,
) -> tuple[bytes, str]:
    """Text-to-speech with fallback. Returns (audio_bytes, mimetype).

    For voice cloning, pass ref_audio_b64 and optionally ref_text.
    """
    last_err = None
    for p in _get_providers("tts"):
        try:
            fmt = p.get("response_format", "opus")

            # If we have reference audio, use direct HTTP to pass extra fields
            if ref_audio_b64:
                payload = {
                    "input": text,
                    "voice": voice_name or p.get("voice", "alloy"),
                    "speed": p.get("speed", 1.0),
                    "response_format": fmt,
                    "ref_audio": ref_audio_b64,
                }
                if ref_text:
                    payload["ref_text"] = ref_text
                async with httpx.AsyncClient(timeout=300) as client:
                    resp = await client.post(
                        f"{p['base_url'].rstrip('/')}/audio/speech",
                        json=payload,
                    )
                    resp.raise_for_status()
                    audio_bytes = resp.content
            else:
                client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=300.0)
                resp = await client.audio.speech.create(
                    model=p["model"],
                    voice=voice_name or p.get("voice", "alloy"),
                    input=text,
                    response_format=fmt,
                    speed=p.get("speed", 1.0),
                )
                audio_bytes = resp.content

            mime_map = {
                "opus": "audio/ogg; codecs=opus",
                "mp3": "audio/mpeg",
                "aac": "audio/aac",
                "flac": "audio/flac",
                "wav": "audio/wav",
            }
            return audio_bytes, mime_map.get(fmt, "audio/ogg")
        except Exception as e:
            log.warning("[tts] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All TTS providers failed. Last: {last_err}")


async def synthesize_dialogue(
    segments: list[dict],
    pause_ms: int = 500,
) -> tuple[bytes, str]:
    """Multi-voice dialogue synthesis via /v1/audio/dialogue.

    Each segment: {"voice": "af_heart", "text": "Hello!"}
    Optionally include ref_audio/ref_text for voice cloning segments.
    Returns (audio_bytes, mimetype).
    """
    last_err = None
    for p in _get_providers("tts"):
        try:
            fmt = p.get("response_format", "opus")
            payload = {
                "segments": segments,
                "speed": p.get("speed", 1.0),
                "response_format": fmt,
                "pause_ms": pause_ms,
            }
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    f"{p['base_url'].rstrip('/')}/audio/dialogue",
                    json=payload,
                )
                resp.raise_for_status()
                audio_bytes = resp.content

            mime_map = {
                "opus": "audio/ogg; codecs=opus",
                "mp3": "audio/mpeg",
                "aac": "audio/aac",
                "flac": "audio/flac",
                "wav": "audio/wav",
            }
            return audio_bytes, mime_map.get(fmt, "audio/ogg")
        except Exception as e:
            log.warning("[dialogue] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All TTS providers failed for dialogue. Last: {last_err}")
