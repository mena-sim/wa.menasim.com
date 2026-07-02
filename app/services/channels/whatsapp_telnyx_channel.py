from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.logging import get_logger
from app.services import inbound_media
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import normalize_e164, send_whatsapp

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"

_AUDIO_EXT = (".ogg", ".opus", ".amr", ".m4a", ".mp3", ".aac", ".webm")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _phone_from_field(value: Any) -> str:
    """Telnyx SMS webhooks use {\"phone_number\": \"+1...\"}; WhatsApp may use a plain string."""
    if isinstance(value, str):
        raw = value.strip()
        if raw.lower().startswith("whatsapp:"):
            raw = raw.split(":", 1)[1].strip()
        return normalize_e164(raw)
    if isinstance(value, dict):
        for key in ("phone_number", "number", "msisdn", "id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return normalize_e164(candidate)
    return ""


def _extract_text(payload: dict[str, Any]) -> str:
    text = payload.get("text") or payload.get("body") or ""
    if not text and isinstance(payload.get("whatsapp_message"), dict):
        wm = payload["whatsapp_message"]
        if wm.get("type") == "text" and isinstance(wm.get("text"), dict):
            text = wm["text"].get("body") or ""
    return str(text or "").strip()


def _guess_type_from_name(name: str) -> str:
    lower = (name or "").lower()
    for ext in _AUDIO_EXT:
        if lower.endswith(ext):
            return "audio"
    for ext in _IMAGE_EXT:
        if lower.endswith(ext):
            return "image"
    if lower.endswith(".pdf") or lower.endswith(".doc") or lower.endswith(".docx"):
        return "document"
    if lower.endswith(".mp4") or lower.endswith(".mov"):
        return "video"
    return ""


def _classify_media(
    *,
    media_url: str | None,
    content_type: str,
    wm_type: str,
    filename: str,
) -> tuple[bool, bool, bool]:
    """Return (is_audio, is_image, is_unsupported)."""
    ct = (content_type or "").lower()
    name_kind = _guess_type_from_name(filename)
    wmt = (wm_type or "").lower()

    if wmt == "audio" or ct.startswith("audio/") or ct in ("application/ogg",) or name_kind == "audio":
        return True, False, False
    if wmt == "image" or ct.startswith("image/") or name_kind == "image":
        return False, True, False
    if wmt in ("document", "video", "sticker") or name_kind in ("document", "video"):
        return False, False, True
    if media_url and not ct.startswith("image/") and not ct.startswith("audio/"):
        # Unknown attachment with a URL — only images accepted for non-audio.
        if name_kind == "image":
            return False, True, False
        if name_kind == "audio":
            return True, False, False
        if name_kind:
            return False, False, True
    return False, False, bool(media_url and not ct.startswith("image/") and not ct.startswith("audio/"))


def parse_inbound(body: dict[str, Any]) -> InboundMessage | None:
    """Parse a Telnyx webhook body into a normalized inbound message.

    Returns None for non-inbound events (delivery receipts, etc.).
    """
    data = body.get("data") or {}
    event_type = str(data.get("event_type") or "")
    if event_type not in ("message.received",):
        return None

    payload = data.get("payload") or {}
    direction = str(payload.get("direction") or "").lower()
    if direction and direction not in ("inbound", "incoming"):
        return None

    sender = _phone_from_field(payload.get("from"))
    if not sender:
        return None

    text = _extract_text(payload)
    media_url = None
    content_type = ""
    filename = ""
    wm_type = ""

    media = payload.get("media") or []
    if isinstance(media, list) and media:
        first = media[0] or {}
        media_url = first.get("url")
        content_type = str(first.get("content_type") or "")
        filename = str(first.get("filename") or first.get("name") or "")

    wm = payload.get("whatsapp_message") or {}
    if isinstance(wm, dict):
        wm_type = str(wm.get("type") or "")
        if wm_type == "audio" and isinstance(wm.get("audio"), dict):
            audio = wm["audio"]
            media_url = audio.get("link") or media_url
            filename = filename or str(audio.get("filename") or "")
            content_type = content_type or "audio/ogg"
        elif wm_type == "image" and isinstance(wm.get("image"), dict):
            image = wm["image"]
            media_url = image.get("link") or media_url
            filename = filename or str(image.get("filename") or "")
            content_type = content_type or "image/jpeg"
        elif wm_type == "document" and isinstance(wm.get("document"), dict):
            doc = wm["document"]
            media_url = doc.get("link") or media_url
            filename = filename or str(doc.get("filename") or "")
        elif wm_type == "video" and isinstance(wm.get("video"), dict):
            vid = wm["video"]
            media_url = vid.get("link") or media_url
            filename = filename or str(vid.get("filename") or "")

    is_audio, is_image, is_unsupported = _classify_media(
        media_url=media_url,
        content_type=content_type,
        wm_type=wm_type,
        filename=filename,
    )

    event_id = str(payload.get("id") or data.get("id") or "")
    return InboundMessage(
        channel=WHATSAPP_CHANNEL,
        sender_id=sender,
        text=text,
        media_url=media_url,
        is_image=is_image,
        is_audio=is_audio,
        is_unsupported_media=is_unsupported,
        media_content_type=content_type or None,
        event_id=event_id or None,
    )


def process_inbound(db: Session, inbound: InboundMessage) -> dict[str, Any]:
    """Run the agent on an inbound WhatsApp message and send the reply back."""
    from app.agent.engine import get_or_create_conversation

    convo = get_or_create_conversation(db, WHATSAPP_CHANNEL, inbound.sender_id)
    if convo.handed_over:
        convo.handed_over = False
        db.commit()

    prepared = inbound_media.prepare_inbound_media(db, inbound)
    if isinstance(prepared, str):
        send_whatsapp(db, inbound.sender_id, prepared)
        return {"replied": True, "media_rejected": True, "escalated": False}
    inbound = prepared

    result = handle_message(
        db,
        channel=inbound.channel,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
    )
    if result.suppressed or not (result.reply or "").strip():
        return {"replied": False, "suppressed": result.suppressed, "escalated": result.escalated}
    send_result = send_whatsapp(db, inbound.sender_id, result.reply, media_url=result.media_url)
    if not send_result.get("ok"):
        logger.warning(
            "whatsapp reply delivery failed to=%s status=%s detail=%s",
            inbound.sender_id,
            send_result.get("status"),
            (send_result.get("detail") or send_result.get("error") or "")[:300],
        )
    return {
        "replied": True,
        "escalated": result.escalated,
        "send": send_result,
    }
