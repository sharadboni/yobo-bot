"""LangGraph state definition."""
from __future__ import annotations
from typing import Any
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """State passed through the LangGraph pipeline."""
    # Inbound message from gateway
    inbound: dict             # raw WS payload
    user_jid: str             # sender's JID
    push_name: str            # WhatsApp display name
    admin_jid: str            # admin's JID

    # User profile (loaded/created by load_user)
    user_profile: dict

    # Resolved content after input normalization
    resolved_text: str        # final text for LLM
    content_type: str         # text | audio | image

    # Intent classification
    intent: str               # skill name to execute
    intent_args: str          # any arguments after the command

    # Skill output
    reply_text: str           # text to send back
    reply_audio: str          # base64-encoded audio (TTS), empty if none
    reply_audio_mimetype: str # mimetype of reply audio
    outbound: list[dict]      # messages to send via WS
