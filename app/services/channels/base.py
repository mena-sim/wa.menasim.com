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
    is_audio: bool = False  # voice note / audio attachment (transcribe before agent)
    is_unsupported_media: bool = False  # PDF, document, video — not accepted
    media_content_type: str | None = None
    event_id: str | None = None  # for idempotency (webhook dedupe)
    parse_debug: dict | None = None  # Telnyx payload shape / parser notes
