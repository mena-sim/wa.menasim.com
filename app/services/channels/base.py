from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InboundMessage:
    """Normalized inbound message from any channel."""

    channel: str
    sender_id: str
    text: str = ""
    media_url: str | None = None
    is_image: bool = False
    event_id: str | None = None  # for idempotency (webhook dedupe)
