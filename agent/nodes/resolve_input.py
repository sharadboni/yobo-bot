"""Normalize input: text passthrough, audio→STT, image→vision, document→text extraction.
Also handles pending voice clone intercept."""
from __future__ import annotations
import logging
import base64
import tempfile
import os
from agent.services.llm import transcribe_audio, vision_completion
from agent.constants import MAX_DOC_CHARS
from agent.sanitize import sanitize_tool_output, wrap_tool_result
from agent.services.voice_store import (
    get_pending_voice, clear_pending_voice,
    add_custom_voice, set_active_voice,
)

log = logging.getLogger(__name__)

SUPPORTED_DOC_MIMES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/html",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
}


def _extract_document_text(data_b64: str, mimetype: str, filename: str) -> str:
    """Extract text content from a document."""
    raw = base64.b64decode(data_b64)

    if mimetype == "application/pdf":
        import fitz  # pymupdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            tmp_path = f.name
        try:
            doc = fitz.open(tmp_path)
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
            text = "\n\n".join(pages)
        finally:
            os.unlink(tmp_path)
    else:
        # Text-based files — decode as UTF-8
        text = raw.decode("utf-8", errors="replace")

    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + f"\n\n[Truncated — showing first {MAX_DOC_CHARS} characters of {len(text)}]"
    return text


async def resolve_input_node(state: dict) -> dict:
    content = state["inbound"].get("content", {})
    ctype = content.get("type", "text")
    user_jid = state.get("user_jid", "")

    if ctype == "text":
        return {
            "resolved_text": content.get("text", ""),
            "content_type": "text",
        }

    if ctype == "audio":
        # Check for pending voice clone first
        pending = get_pending_voice(user_jid)
        if pending:
            audio_b64 = content.get("data", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                add_custom_voice(user_jid, pending["name"], audio_bytes, pending.get("ref_text", ""))
                set_active_voice(user_jid, pending["name"])
                clear_pending_voice(user_jid)
                return {
                    "resolved_text": "",
                    "content_type": "audio",
                    "reply_text": f"Voice *{pending['name']}* saved and set as active!",
                    "intent": "__voice_clone__",
                }

        # Normal audio → STT
        try:
            audio_bytes = base64.b64decode(content["data"])
            text = await transcribe_audio(audio_bytes, content.get("mimetype", "audio/ogg"))
            return {"resolved_text": text, "content_type": "audio"}
        except Exception as e:
            log.error("STT failed: %s", e, exc_info=True)
            return {
                "resolved_text": "",
                "content_type": "audio",
                "reply_text": "Sorry, I couldn't process that audio.",
                "intent": "__error__",
            }

    if ctype == "image":
        caption = content.get("caption", "")
        try:
            img_b64 = content["data"]
            prompt = caption or "Describe this image and respond helpfully."
            description = await vision_completion(img_b64, prompt)
            return {"resolved_text": description, "content_type": "image"}
        except Exception as e:
            log.error("Vision failed: %s", e, exc_info=True)
            return {
                "resolved_text": caption,
                "content_type": "image",
                "reply_text": "Sorry, I couldn't process that image.",
                "intent": "__error__",
            }

    if ctype == "document":
        mimetype = content.get("mimetype", "")
        filename = content.get("filename", "document")
        caption = content.get("caption", "")

        # Check if we can extract text from this file type
        if mimetype not in SUPPORTED_DOC_MIMES:
            return {
                "resolved_text": caption,
                "content_type": "text",
                "reply_text": f"Sorry, I can't process {mimetype} files. I support PDF, plain text, CSV, HTML, Markdown, and JSON.",
                "intent": "__error__",
            }

        try:
            doc_text = _extract_document_text(content["data"], mimetype, filename)
            if not doc_text.strip():
                return {
                    "resolved_text": caption,
                    "content_type": "text",
                    "reply_text": "The document appears to be empty or contains no extractable text.",
                    "intent": "__error__",
                }

            # Sanitize document content (untrusted external data)
            doc_text = sanitize_tool_output(doc_text, source=f"document:{filename}")
            doc_wrapped = wrap_tool_result(doc_text, f"document:{filename}")

            # Combine caption (user instruction) with sanitized document content
            instruction = caption or "Summarize this document."
            resolved = f"{instruction}\n\n{doc_wrapped}"
            log.info("Document extracted: file=%s mime=%s chars=%d caption=%r", filename, mimetype, len(doc_text), caption)
            return {"resolved_text": resolved, "content_type": "text"}
        except Exception as e:
            log.error("Document extraction failed: %s", e, exc_info=True)
            return {
                "resolved_text": caption,
                "content_type": "text",
                "reply_text": "Sorry, I couldn't read that document.",
                "intent": "__error__",
            }

    return {"resolved_text": str(content), "content_type": "unknown"}
