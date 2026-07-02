from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.logging import get_logger
from app.services import runtime_config, transcription
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import send_whatsapp

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"


def _phone_from_field(value: Any) -> str:
    """Telnyx SMS webhooks use {\"phone_number\": \"+1...\"}; WhatsApp may use a plain string."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("phone_number") or "").strip()
    return ""


def parse_inbound(body: dict[str, Any]) -> InboundMessage | None:
    """Parse a Telnyx webhook body into a normalized inbound message.

    Returns None for non-inbound events (delivery receipts, etc.).
    """
    data = body.get("data") or {}
    event_type = str(data.get("event_type") or "")
    if event_type not in ("message.received",):
        return None

    payload = data.get("payload") or {}
    sender = _phone_from_field(payload.get("from"))
    if not sender:
        return None

    text = str(payload.get("text") or "").strip()
    media = payload.get("media") or []
    media_url = None
    content_type = ""
    is_image = False
    is_audio = False
    if isinstance(media, list) and media:
        first = media[0] or {}
        media_url = first.get("url")
        content_type = str(first.get("content_type") or "")
        # WhatsApp voice notes arrive as audio/ogg; transcribe them.
        is_audio = content_type.startswith("audio/")
        # Any other (non-audio) media is an attachment the agent can't read.
        is_image = (not is_audio) and bool(media_url)

    event_id = str(payload.get("id") or data.get("id") or "")
    return InboundMessage(
        channel=WHATSAPP_CHANNEL,
        sender_id=sender,
        text=text,
        media_url=media_url,
        is_image=is_image,
        is_audio=is_audio,
        media_content_type=content_type or None,
        event_id=event_id or None,
    )


def _transcribe_media(db: Session, url: str, content_type: str | None) -> str | None:
    """Download a WhatsApp audio media URL and transcribe it (DeepInfra Whisper)."""
    if not runtime_config.voice_enabled(db):
        return None
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            audio = resp.content
            ct = content_type or resp.headers.get("content-type") or "audio/ogg"
        ext = "ogg" if "ogg" in ct else ("mp3" if "mpeg" in ct or "mp3" in ct else "m4a")
        tr = transcription.transcribe(db, audio, filename=f"voice.{ext}", content_type=ct)
        return (tr.get("text") or "").strip() or None
    except (httpx.HTTPError, transcription.TranscriptionError):
        logger.exception("whatsapp voice transcription failed")
        return None
    except Exception:  # never let a media issue crash the webhook
        logger.exception("unexpected error transcribing whatsapp voice")
        return None


def process_inbound(db: Session, inbound: InboundMessage) -> dict[str, Any]:
    """Run the agent on an inbound WhatsApp message and send the reply back."""
    # Voice notes: transcribe to text (auto language) before the agent sees them.
    if inbound.is_audio and inbound.media_url:
        transcript = _transcribe_media(db, inbound.media_url, inbound.media_content_type)
        if transcript:
            inbound.text = (f"{inbound.text}\n{transcript}".strip() if inbound.text else transcript)
            inbound.is_image = False
            inbound.is_audio = False
            inbound.media_url = None
        else:
            # Couldn't transcribe (voice disabled or error): ask them to type. No LLM call.
            msg = (
                "🎙️ عذرًا، تعذّر تحويل الرسالة الصوتية إلى نص. من فضلك اكتب سؤالك.\n"
                "Sorry, I couldn't process that voice note. Please type your question."
            )
            send_whatsapp(db, inbound.sender_id, msg)
            return {"replied": True, "transcribed": False, "escalated": False}

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
