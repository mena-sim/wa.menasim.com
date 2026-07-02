from __future__ import annotations

import ast
import json
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
_MEDIA_KEYS = ("audio", "image", "video", "document", "sticker")


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


def _parse_maybe_dict(value: Any) -> dict[str, Any] | None:
    """Parse dict payloads Telnyx sometimes embeds as JSON or Python repr strings."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    try:
        obj = ast.literal_eval(s)
        return obj if isinstance(obj, dict) else None
    except (ValueError, SyntaxError):
        return None


def _looks_like_whatsapp_message(obj: dict[str, Any]) -> bool:
    if str(obj.get("type") or "") in {"text", "audio", "image", "video", "document", "sticker"}:
        return True
    return any(isinstance(obj.get(key), dict) for key in _MEDIA_KEYS) or isinstance(obj.get("text"), dict)


def _resolve_whatsapp_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Telnyx/Meta WhatsApp message shapes into one dict."""
    wm = payload.get("whatsapp_message")
    parsed = _parse_maybe_dict(wm)
    if parsed and _looks_like_whatsapp_message(parsed):
        return parsed
    if isinstance(wm, dict) and _looks_like_whatsapp_message(wm):
        return wm

    # Some Telnyx webhooks put the whole inbound WA object in payload.text as a stringified dict.
    parsed_text = _parse_maybe_dict(payload.get("text"))
    if parsed_text and _looks_like_whatsapp_message(parsed_text):
        return parsed_text
    return {}


def _media_url_from_attachment(attachment: dict[str, Any]) -> str | None:
    for key in ("link", "url", "href"):
        val = attachment.get(key)
        if val:
            return str(val)
    return None


def _infer_wm_type(wm: dict[str, Any]) -> str:
    wm_type = str(wm.get("type") or "").lower()
    if wm_type:
        return wm_type
    for kind in _MEDIA_KEYS:
        if isinstance(wm.get(kind), dict):
            return kind
    if isinstance(wm.get("text"), dict):
        return "text"
    return ""


def _extract_text(payload: dict[str, Any], wm: dict[str, Any]) -> str:
    if wm:
        if _infer_wm_type(wm) == "text" and isinstance(wm.get("text"), dict):
            return str(wm["text"].get("body") or "").strip()
        if isinstance(wm.get("text"), dict):
            return str(wm["text"].get("body") or "").strip()
        if isinstance(wm.get("text"), str):
            return wm["text"].strip()

    text = payload.get("text") or payload.get("body") or ""
    if isinstance(text, str) and not _parse_maybe_dict(text):
        return str(text).strip()
    return ""


def _extract_media_from_wm(
    wm: dict[str, Any],
) -> tuple[str | None, str, str, str]:
    """Return (media_url, content_type, filename, wm_type)."""
    wm_type = _infer_wm_type(wm)
    if wm_type in _MEDIA_KEYS and isinstance(wm.get(wm_type), dict):
        block = wm[wm_type]
        media_url = _media_url_from_attachment(block)
        content_type = str(block.get("mime_type") or block.get("content_type") or "")
        filename = str(block.get("filename") or block.get("name") or "")
        if wm_type == "audio" and not content_type:
            content_type = "audio/ogg"
        if wm_type == "image" and not content_type:
            content_type = "image/jpeg"
        return media_url, content_type, filename, wm_type
    return None, "", "", wm_type


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
    # WhatsApp voice notes often have media URL but no content_type — treat as audio, not image.
    if media_url and not ct and not name_kind and not wmt:
        return True, False, False
    if media_url and not ct.startswith("image/") and not ct.startswith("audio/"):
        if name_kind == "image":
            return False, True, False
        if name_kind == "audio":
            return True, False, False
        if name_kind:
            return False, False, True
    return False, False, False


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
        wm_early = _resolve_whatsapp_message(payload)
        sender = _phone_from_field(wm_early.get("from"))
    if not sender:
        return None

    wm = _resolve_whatsapp_message(payload)
    text = _extract_text(payload, wm)
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

    if wm:
        wm_media_url, wm_content_type, wm_filename, wm_type = _extract_media_from_wm(wm)
        media_url = wm_media_url or media_url
        content_type = wm_content_type or content_type
        filename = wm_filename or filename

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

    parse_log = {
        "parsed_audio": inbound.is_audio,
        "parsed_image": inbound.is_image,
        "parsed_unsupported": inbound.is_unsupported_media,
        "content_type": inbound.media_content_type or "",
        "has_media_url": bool(inbound.media_url),
        "text_len": len(inbound.text or ""),
    }

    prepared, media_log = inbound_media.prepare_inbound_media(db, inbound)
    media_log = {"parse": parse_log, **media_log}

    if isinstance(prepared, str):
        send_whatsapp(db, inbound.sender_id, prepared)
        return {
            "replied": True,
            "media_rejected": True,
            "escalated": False,
            "media_log": media_log,
        }
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
        return {
            "replied": False,
            "suppressed": result.suppressed,
            "escalated": result.escalated,
            "media_log": media_log,
        }
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
        "media_log": media_log,
        "conversation_id": result.conversation_id,
    }
