from __future__ import annotations

import ast
import json
import re
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
_URL_IN_TEXT_RE = re.compile(
    r"""['"](?:url|link)['"]\s*:\s*['"](https?://[^'"]+)['"]""",
    re.IGNORECASE,
)
_URL_IN_TEXT_TRUNC_RE = re.compile(
    r"""['"](?:url|link)['"]\s*:\s*['"](https?://[^\s'"}]+)""",
    re.IGNORECASE,
)
_MIME_IN_TEXT_RE = re.compile(
    r"""['"]mime_type['"]\s*:\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_BODY_IN_TEXT_RE = re.compile(
    r"""['"]body['"]\s*:\s*['"]((?:\\.|[^'\\])*)['"]""",
    re.IGNORECASE,
)
# Nested Meta shape: 'text': {'body': 'hello'}
_NESTED_BODY_RE = re.compile(
    r"""['"]text['"]\s*:\s*\{[^}]*['"]body['"]\s*:\s*['"]([^'"]*)['"]""",
    re.IGNORECASE | re.DOTALL,
)
# Truncated dicts (no closing quote)
_BODY_TRUNC_RE = re.compile(
    r"""['"]body['"]\s*:\s*['"]([^'"]{1,2000})""",
    re.IGNORECASE,
)


def _text_from_message_obj(obj: dict[str, Any]) -> str:
    """Extract user-visible text from a Meta/Telnyx WhatsApp message object."""
    if not obj:
        return ""
    if isinstance(obj.get("text"), dict):
        body = str(obj["text"].get("body") or "").strip()
        if body:
            return body
    if isinstance(obj.get("text"), str):
        t = obj["text"].strip()
        if t and not t.startswith("{"):
            return t
    body = str(obj.get("body") or "").strip()
    if body and not body.startswith("{"):
        return body
    return ""


def _text_from_any_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return _text_from_message_obj(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if s.startswith("{"):
            parsed = _parse_maybe_dict(s)
            if parsed:
                body = _text_from_message_obj(parsed)
                if body:
                    return body
            body = _extract_text_body_from_blob(s)
            if body:
                return body
            return ""
        return s
    return ""


def _deep_find_text_body(obj: Any, depth: int = 0) -> str:
    """Walk the webhook payload when text is not in the obvious fields."""
    if depth > 10:
        return ""
    if isinstance(obj, dict):
        if not any(isinstance(obj.get(k), dict) for k in _MEDIA_KEYS):
            body = _text_from_message_obj(obj)
            if body:
                return body
        for key, value in obj.items():
            if key in _MEDIA_KEYS:
                continue
            found = _deep_find_text_body(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_text_body(item, depth + 1)
            if found:
                return found
    elif isinstance(obj, str) and obj.strip().startswith("{"):
        parsed = _parse_maybe_dict(obj)
        if parsed:
            return _deep_find_text_body(parsed, depth + 1)
    return ""


def _serialize_raw_text(raw_text: Any) -> str:
    if raw_text is None:
        return ""
    if isinstance(raw_text, dict):
        try:
            return json.dumps(raw_text, ensure_ascii=False)[:4000]
        except (TypeError, ValueError):
            return str(raw_text)[:4000]
    return str(raw_text)[:4000]


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


def _extract_from_text_blob(text: str) -> tuple[str | None, str, str, str]:
    """Best-effort parse when payload.text is a broken Python/JSON dict string."""
    blob = (text or "").strip()
    if not blob.startswith("{"):
        return None, "", "", ""

    parsed = _parse_maybe_dict(blob)
    if parsed and _looks_like_whatsapp_message(parsed):
        return _extract_media_from_wm(parsed)

    media_url = None
    mime = ""
    wm_type = ""
    if re.search(r"""['"]audio['"]\s*:""", blob):
        wm_type = "audio"
        m = _URL_IN_TEXT_RE.search(blob) or _URL_IN_TEXT_TRUNC_RE.search(blob)
        if m:
            media_url = m.group(1)
        m = _MIME_IN_TEXT_RE.search(blob)
        if m:
            mime = m.group(1)
    elif re.search(r"""['"]image['"]\s*:""", blob):
        wm_type = "image"
        m = _URL_IN_TEXT_RE.search(blob) or _URL_IN_TEXT_TRUNC_RE.search(blob)
        if m:
            media_url = m.group(1)
        m = _MIME_IN_TEXT_RE.search(blob)
        if m:
            mime = m.group(1)

    return media_url, mime, "", wm_type


def _extract_text_body_from_blob(text: str) -> str:
    parsed = _parse_maybe_dict(text)
    if parsed:
        body = _extract_text({"text": ""}, parsed)
        if body:
            return body
    for pattern in (_NESTED_BODY_RE, _BODY_IN_TEXT_RE, _BODY_TRUNC_RE):
        m = pattern.search(text or "")
        if m:
            return m.group(1).strip()
    return ""


def _raw_text_looks_like_audio(raw: str) -> bool:
    return bool(re.search(r"""['"]audio['"]\s*:""", raw or ""))


def repair_inbound(inbound: InboundMessage) -> InboundMessage:
    """Recover text/voice from Telnyx dict blobs stored in parse_debug.raw_text."""
    raw = (inbound.parse_debug or {}).get("raw_text") or ""
    if not raw:
        return inbound
    if not (inbound.text or "").strip():
        body = _text_from_any_value(raw)
        if not body:
            body = _extract_text_body_from_blob(raw)
        if body:
            inbound.text = body
    if not inbound.media_url and _raw_text_looks_like_audio(raw):
        blob_url, blob_ct, _fn, blob_type = _extract_from_text_blob(raw)
        if blob_url and blob_type == "audio":
            inbound.media_url = blob_url
            inbound.media_content_type = blob_ct or "audio/ogg"
            inbound.is_audio = True
            inbound.is_image = False
    return inbound


def _find_media_in_obj(obj: Any, found: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """Recursively collect media URLs anywhere in a Telnyx webhook payload."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        url = None
        for key in ("url", "link", "href"):
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("http"):
                url = val
                break
        if url:
            found.append(
                {
                    "url": url,
                    "content_type": str(obj.get("mime_type") or obj.get("content_type") or ""),
                    "filename": str(obj.get("filename") or obj.get("name") or ""),
                    "kind": str(obj.get("type") or ""),
                }
            )
        for value in obj.values():
            _find_media_in_obj(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_media_in_obj(item, found)
    elif isinstance(obj, str) and obj.strip().startswith("{"):
        parsed = _parse_maybe_dict(obj)
        if parsed:
            _find_media_in_obj(parsed, found)
    return found


def _pick_media_candidate(candidates: list[dict[str, str]]) -> dict[str, str] | None:
    if not candidates:
        return None
    for item in candidates:
        ct = (item.get("content_type") or "").lower()
        if ct.startswith("audio/"):
            return item
    for item in candidates:
        if "audio" in (item.get("kind") or "").lower():
            return item
    for item in candidates:
        url = (item.get("url") or "").lower()
        if any(ext in url for ext in _AUDIO_EXT):
            return item
    for item in candidates:
        ct = (item.get("content_type") or "").lower()
        if ct.startswith("image/"):
            return item
    return candidates[0]


def payload_debug_summary(payload: dict[str, Any]) -> dict[str, Any]:
    wm = payload.get("whatsapp_message")
    text = payload.get("text")
    summary = {
        "payload_type": str(payload.get("type") or ""),
        "payload_keys": sorted(payload.keys()),
        "text_type": type(text).__name__,
        "text_len": len(str(text or "")),
        "text_preview": str(text or "")[:180],
        "whatsapp_message_type": type(wm).__name__ if wm is not None else "missing",
        "media_count": len(payload.get("media") or []) if isinstance(payload.get("media"), list) else 0,
    }
    if isinstance(payload.get("media"), list) and payload["media"]:
        first = payload["media"][0] or {}
        summary["media0_content_type"] = str(first.get("content_type") or "")
        summary["media0_has_url"] = bool(first.get("url"))
    return summary


def _looks_like_whatsapp_message(obj: dict[str, Any]) -> bool:
    if str(obj.get("type") or "") in {"text", "audio", "image", "video", "document", "sticker"}:
        return True
    if isinstance(obj.get("body"), str) and obj.get("body", "").strip():
        return True
    return any(isinstance(obj.get(key), dict) for key in _MEDIA_KEYS) or isinstance(
        obj.get("text"), (dict, str)
    )


def _resolve_whatsapp_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Telnyx/Meta WhatsApp message shapes into one dict."""
    for key in ("whatsapp_message", "message", "content"):
        wm = payload.get(key)
        parsed = _parse_maybe_dict(wm)
        if parsed and _looks_like_whatsapp_message(parsed):
            return parsed
        if isinstance(wm, dict) and _looks_like_whatsapp_message(wm):
            return wm

    # Some Telnyx webhooks put the whole inbound WA object in payload.text as a stringified dict.
    parsed_text = _parse_maybe_dict(payload.get("text"))
    if parsed_text and _looks_like_whatsapp_message(parsed_text):
        return parsed_text
    text_val = payload.get("text")
    if isinstance(text_val, dict) and _looks_like_whatsapp_message(text_val):
        return text_val
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
        body = _text_from_message_obj(wm)
        if body:
            return body

    for key in ("text", "body", "content"):
        body = _text_from_any_value(payload.get(key))
        if body:
            return body

    return _deep_find_text_body(payload)


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

    parse_notes: list[str] = []
    raw_text = payload.get("text")
    if not text and raw_text is not None:
        body_from_raw = _text_from_any_value(raw_text)
        if body_from_raw:
            text = body_from_raw
            parse_notes.append("text_from_raw")

    if not text and isinstance(raw_text, str) and raw_text.strip().startswith("{"):
        body_from_blob = _extract_text_body_from_blob(raw_text)
        if body_from_blob:
            text = body_from_blob
            parse_notes.append("text_from_blob")

    if not text:
        deep_body = _deep_find_text_body(payload)
        if deep_body:
            text = deep_body
            parse_notes.append("text_from_payload_scan")

    if not media_url and raw_text is not None:
        raw_for_blob = _serialize_raw_text(raw_text)
        if raw_for_blob.strip().startswith("{"):
            blob_url, blob_ct, blob_fn, blob_type = _extract_from_text_blob(raw_for_blob)
            if blob_url:
                media_url = blob_url
                content_type = blob_ct or content_type
                filename = blob_fn or filename
                wm_type = blob_type or wm_type
                parse_notes.append("media_from_text_blob")

    if not media_url:
        picked = _pick_media_candidate(_find_media_in_obj(payload))
        if picked:
            media_url = picked.get("url") or media_url
            content_type = picked.get("content_type") or content_type
            filename = picked.get("filename") or filename
            if not wm_type and (picked.get("content_type") or "").lower().startswith("audio/"):
                wm_type = "audio"
            elif not wm_type and any(
                ext in (picked.get("url") or "").lower() for ext in _AUDIO_EXT
            ):
                wm_type = "audio"
            parse_notes.append("media_from_payload_scan")

    is_audio, is_image, is_unsupported = _classify_media(
        media_url=media_url,
        content_type=content_type,
        wm_type=wm_type,
        filename=filename,
    )

    parse_debug = payload_debug_summary(payload)
    if raw_text is not None:
        parse_debug["raw_text"] = _serialize_raw_text(raw_text)
    if parse_notes:
        parse_debug["parse_notes"] = parse_notes
    if (
        not media_url
        and raw_text is not None
        and _raw_text_looks_like_audio(_serialize_raw_text(raw_text))
    ):
        parse_debug["parse_error"] = "audio_dict_in_text_but_no_url_extracted"

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
        parse_debug=parse_debug,
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
    if inbound.parse_debug:
        parse_log["payload_debug"] = inbound.parse_debug

    inbound = repair_inbound(inbound)
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

    # If voice still not handled, retry once after repair (e.g. URL extracted from raw_text).
    if inbound.is_audio and inbound.media_url and media_log.get("action") == "none":
        inbound = repair_inbound(inbound)
        prepared2, media_log2 = inbound_media.prepare_inbound_media(db, inbound)
        media_log["retry"] = media_log2
        if isinstance(prepared2, str):
            send_whatsapp(db, inbound.sender_id, prepared2)
            return {
                "replied": True,
                "media_rejected": True,
                "escalated": False,
                "media_log": media_log,
            }
        inbound = prepared2

    was_audio = bool(
        inbound.parse_debug
        and (
            inbound.parse_debug.get("parse_error") == "audio_dict_in_text_but_no_url_extracted"
            or "media_from_text_blob" in (inbound.parse_debug.get("parse_notes") or [])
            or re.search(r"""['"]audio['"]\s*:""", inbound.parse_debug.get("raw_text") or "")
        )
    )
    result = handle_message(
        db,
        channel=inbound.channel,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
        was_audio_attempt=was_audio or media_log.get("action", "").startswith("transcribe"),
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
