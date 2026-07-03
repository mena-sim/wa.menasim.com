from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import normalize_e164
from app.services.whatsapp.providers import whatsapp_from_number

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"
GRAPH_API_VERSION = "v21.0"


class MetaWebhookVerificationError(ValueError):
    pass


def _token(db: Session) -> str:
    return runtime_config.get(db, "meta_whatsapp_token")


def _phone_number_id(db: Session) -> str:
    return runtime_config.get(db, "meta_phone_number_id")


def verify_subscription(mode: str | None, token: str | None, challenge: str | None, db: Session) -> str | None:
    expected = runtime_config.get(db, "meta_verify_token")
    if mode == "subscribe" and token and expected and token == expected:
        return challenge or ""
    return None


def verify_webhook(db: Session, raw_body: bytes, signature_header: str | None) -> bool:
    secret = runtime_config.get(db, "meta_app_secret")
    if not secret:
        logger.debug("[meta-verify] skipped (no app secret configured)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        raise MetaWebhookVerificationError("Missing or invalid X-Hub-Signature-256 header")
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.split("=", 1)[1]
    if not hmac.compare_digest(expected, received):
        raise MetaWebhookVerificationError("Invalid Meta webhook signature")
    return True


def _message_from_meta(msg: dict[str, Any]) -> InboundMessage | None:
    sender = normalize_e164(str(msg.get("from") or ""))
    if not sender:
        return None

    msg_type = str(msg.get("type") or "text").lower()
    text = ""
    media_url = None
    content_type = ""
    is_image = False
    is_audio = False
    is_unsupported = False

    if msg_type == "text":
        text = str((msg.get("text") or {}).get("body") or "").strip()
    elif msg_type == "audio":
        audio = msg.get("audio") or {}
        media_url = str(audio.get("url") or audio.get("link") or "") or None
        content_type = str(audio.get("mime_type") or "audio/ogg")
        is_audio = True
    elif msg_type == "image":
        image = msg.get("image") or {}
        media_url = str(image.get("url") or image.get("link") or "") or None
        content_type = str(image.get("mime_type") or "image/jpeg")
        is_image = True
        text = str(image.get("caption") or "").strip()
    else:
        is_unsupported = True

    return InboundMessage(
        channel=WHATSAPP_CHANNEL,
        sender_id=sender,
        text=text,
        media_url=media_url,
        is_image=is_image,
        is_audio=is_audio,
        is_unsupported_media=is_unsupported,
        media_content_type=content_type or None,
        event_id=str(msg.get("id") or "") or None,
        parse_debug={"provider": "meta", "message_type": msg_type},
    )


def parse_inbound(body: dict[str, Any]) -> list[InboundMessage]:
    if str(body.get("object") or "") != "whatsapp_business_account":
        return []
    messages: list[InboundMessage] = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                inbound = _message_from_meta(msg)
                if inbound:
                    messages.append(inbound)
    return messages


def send_whatsapp(
    db: Session, to: str, text: str, *, media_url: str | None = None
) -> dict[str, Any]:
    token = _token(db)
    phone_number_id = _phone_number_id(db)
    to_digits = normalize_e164(to).lstrip("+")
    if not token or not phone_number_id or not to_digits:
        return {"ok": False, "skipped": True, "reason": "meta_not_configured"}

    if media_url:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to_digits,
            "type": "image",
            "image": {"link": media_url},
        }
        if text:
            payload["image"]["caption"] = text
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_digits,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
        ok = resp.status_code < 300
        detail = resp.text[:500] if not ok else ""
        if not ok:
            logger.warning("[meta] send failed %s: %s", resp.status_code, detail)
        return {"ok": ok, "status": resp.status_code, "detail": detail, "provider": "meta"}
    except httpx.HTTPError as exc:
        logger.warning("[meta] send error: %s", exc)
        return {"ok": False, "error": str(exc), "provider": "meta"}


def test_connection(db: Session) -> dict[str, Any]:
    token = _token(db)
    phone_number_id = _phone_number_id(db)
    if not token:
        return {"ok": False, "message": "Meta WhatsApp access token is not set."}
    if not phone_number_id:
        return {"ok": False, "message": "Meta phone number ID is not set."}
    if not runtime_config.get(db, "meta_verify_token"):
        return {
            "ok": False,
            "message": "Meta verify token is not set (needed for webhook subscription).",
        }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}",
                params={"fields": "id,display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code < 300:
            data = resp.json()
            number = data.get("display_phone_number") or whatsapp_from_number(db, "meta")
            return {
                "ok": True,
                "message": f"Meta WhatsApp connected ({number or phone_number_id}).",
                "details": [json.dumps(data, ensure_ascii=False)[:240]],
            }
        return {"ok": False, "message": f"HTTP {resp.status_code}: check Meta token and phone number ID."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}
