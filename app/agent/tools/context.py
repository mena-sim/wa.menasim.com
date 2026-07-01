from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.conversation import Conversation


@dataclass
class ToolContext:
    db: Session
    conversation: Conversation
    language: str = "en"
    # Tools may attach an outbound media URL (e.g. QR image) for the channel to send.
    media_url: str | None = None
    # Set when a tool requests human handoff so the engine can note it.
    escalated: bool = field(default=False)
