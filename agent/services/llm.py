"""LLM service with per-capability fallback across endpoints."""
from __future__ import annotations
import logging
import re
import tempfile
import os
import openai
import httpx
from agent.config import LLM_CONFIG
from agent.sanitize import sanitize_tool_output, wrap_tool_result

log = logging.getLogger(__name__)


def _get_providers(capability: str) -> list[dict]:
    providers = LLM_CONFIG.get(capability, [])
    if not providers:
        raise RuntimeError(f"No providers configured for '{capability}' in llm_config.yaml")
    return providers


def _strip_think(text: str) -> str:
    """Strip <think>...</think> blocks from model output, including incomplete ones."""
    # Complete <think>...</think> blocks
    result = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    # Incomplete <think> block (truncated output) — remove from <think> to end
    result = re.sub(r"<think>.*$", "", result, flags=re.DOTALL)
    return result.strip() or text.strip()


async def chat_completion(messages: list[dict], **overrides) -> str:
    """Text completion with fallback."""
    last_err = None
    for p in _get_providers("text"):
        try:
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"])
            resp = await client.chat.completions.create(
                model=p["model"],
                messages=messages,
                max_tokens=overrides.get("max_tokens", p["max_tokens"]),
                temperature=overrides.get("temperature", p["temperature"]),
            )
            return _strip_think(resp.choices[0].message.content)
        except Exception as e:
            log.warning("[text] %s failed: %s", p["name"], e)
            last_err = e
    raise RuntimeError(f"All text providers failed. Last: {last_err}")


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
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"])
            msgs = list(messages)

            for _ in range(max_rounds):
                resp = await client.chat.completions.create(
                    model=p["model"],
                    messages=msgs,
                    tools=tools,
                    max_tokens=overrides.get("max_tokens", p["max_tokens"]),
                    temperature=overrides.get("temperature", p["temperature"]),
                )
                choice = resp.choices[0]

                # No tool calls — return the final text
                if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                    content = choice.message.content or ""
                    return _strip_think(content)

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
                max_tokens=overrides.get("max_tokens", p["max_tokens"]),
                temperature=overrides.get("temperature", p["temperature"]),
            )
            return _strip_think(resp.choices[0].message.content or "")
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
    url = f"{p['base_url'].rstrip('/')}/v1/vision"
    payload = {
        "image": image_b64,
        "prompt": prompt,
        "max_tokens": overrides.get("max_tokens", p.get("max_tokens", 2048)),
        "temperature": overrides.get("temperature", p.get("temperature", 0.7)),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()["text"]


async def _openai_vision(p: dict, image_b64: str, prompt: str, **overrides) -> str:
    """Call OpenAI-compatible chat completions with image."""
    client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"])
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
            client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"])
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
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{p['base_url'].rstrip('/')}/audio/speech",
                        json=payload,
                    )
                    resp.raise_for_status()
                    audio_bytes = resp.content
            else:
                client = openai.AsyncOpenAI(base_url=p["base_url"], api_key=p["api_key"])
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
