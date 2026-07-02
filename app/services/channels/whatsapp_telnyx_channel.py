from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.logging import get_logger
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import send_whatsapp

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"


def parse_inbound(body: dict[str, Any]) -> InboundMessage | None:
    """Parse a Telnyx webhook body into a normalized inbound message.

    Returns None for non-inbound events (delivery receipts, etc.).
    """
    data = body.get("data") or {}
    event_type = str(data.get("event_type") or "")
    if event_type not in ("message.received",):
        return None

    payload = data.get("payload") or {}
    frm = payload.get("from") or {}
    sender = str(frm.get("phone_number") or "").strip()
    if not sender:
        return None

    text = str(payload.get("text") or "").strip()
    media = payload.get("media") or []
    media_url = None
    is_image = False
    if isinstance(media, list) and media:
        first = media[0] or {}
        media_url = first.get("url")
        content_type = str(first.get("content_type") or "")
        is_image = content_type.startswith("image/") or bool(media_url)

    event_id = str(payload.get("id") or data.get("id") or "")
    return InboundMessage(
        channel=WHATSAPP_CHANNEL,
        sender_id=sender,
        text=text,
        media_url=media_url,
        is_image=is_image,
        event_id=event_id or None,
    )


def process_inbound(db: Session, inbound: InboundMessage) -> dict[str, Any]:
    """Run the agent on an inbound WhatsApp message and send the reply back."""
    result = handle_message(
        db,
        channel=inbound.channel,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
    )
    # When a human has taken over, the AI stays silent; don't auto-send.
    if result.suppressed or not (result.reply or "").strip():
        return {"replied": False, "suppressed": result.suppressed, "escalated": result.escalated}
    send_result = send_whatsapp(db, inbound.sender_id, result.reply, media_url=result.media_url)
    return {
        "replied": True,
        "escalated": result.escalated,
        "send": send_result,
    }
