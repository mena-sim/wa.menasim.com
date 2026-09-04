from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.logging import get_logger
from app.services import inbound_media
from app.services.channels.base import InboundMessage
from app.services.whatsapp.twilio_provider import send_sms

logger = get_logger(__name__)

SMS_CHANNEL = "sms"


def process_inbound(db: Session, inbound: InboundMessage) -> dict[str, Any]:
    """Run the agent on an inbound SMS and send the reply back via Twilio SMS."""
    from app.agent.engine import get_or_create_conversation

    inbound.channel = SMS_CHANNEL
    convo = get_or_create_conversation(db, SMS_CHANNEL, inbound.sender_id)
    if convo.handed_over:
        convo.handed_over = False
        db.commit()

    prepared, media_log = inbound_media.prepare_inbound_media(db, inbound)
    if isinstance(prepared, str):
        send_sms(db, inbound.sender_id, prepared)
        return {
            "replied": True,
            "media_rejected": True,
            "escalated": False,
            "media_log": media_log,
            "channel": SMS_CHANNEL,
        }
    inbound = prepared

    result = handle_message(
        db,
        channel=SMS_CHANNEL,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
        was_audio_attempt=bool(inbound.is_audio),
    )
    if result.suppressed or not (result.reply or "").strip():
        return {
            "replied": False,
            "suppressed": result.suppressed,
            "escalated": result.escalated,
            "media_log": media_log,
            "channel": SMS_CHANNEL,
        }
    send_result = send_sms(db, inbound.sender_id, result.reply, media_url=result.media_url)
    if not send_result.get("ok"):
        logger.warning(
            "sms reply delivery failed to=%s status=%s detail=%s",
            inbound.sender_id,
            send_result.get("status"),
            (send_result.get("detail") or send_result.get("error") or "")[:300],
        )
    return {
        "replied": True,
        "escalated": result.escalated,
        "send": send_result,
        "media_log": media_log,
        "conversation_id": result.conversation_id,
        "channel": SMS_CHANNEL,
    }
