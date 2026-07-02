from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)

TELNYX_WHATSAPP_MESSAGES_URL = "https://api.telnyx.com/v2/messages/whatsapp"


class TelnyxWebhookVerificationError(ValueError):
    pass


def verify_webhook(
    db: Session,
    raw_body: bytes,
    *,
    signature_header: str | None,
    timestamp_header: str | None,
) -> bool:
    """Verify Telnyx Ed25519 webhook signature.

    Returns True when verification passes OR when no public key is configured
    (skip in local/dev). Raises on an explicit signature failure.
    Mirrors the voxbulk-api telnyx_webhook_security pattern.
    """
    public_key_b64 = (runtime_config.get(db, "telnyx_webhook_public_key") or "").strip()
    if not public_key_b64:
        logger.debug("[telnyx-verify] skipped (no public key configured)")
        return True
    if not signature_header or not timestamp_header:
        raise TelnyxWebhookVerificationError("Missing Telnyx signature headers")
    try:
        ts = int(str(timestamp_header).strip())
    except ValueError as exc:
        raise TelnyxWebhookVerificationError("Invalid telnyx-timestamp") from exc
    if abs(int(time.time()) - ts) > 300:
        raise TelnyxWebhookVerificationError("Telnyx webhook timestamp too old")

    signed_payload = f"{timestamp_header}|".encode("utf-8") + raw_body
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public_key.verify(base64.b64decode(signature_header), signed_payload)
    except (InvalidSignature, ValueError) as exc:
        raise TelnyxWebhookVerificationError("Invalid Telnyx webhook signature") from exc
    return True


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def normalize_e164(phone: str) -> str:
    """Ensure a phone number is in E.164 (+countrycode...) format."""
    p = (phone or "").strip().replace(" ", "")
    if p and not p.startswith("+"):
        p = f"+{p}"
    return p


def _whatsapp_payload(text: str, *, media_url: str | None = None) -> dict[str, Any]:
    """Build the whatsapp_message body for Telnyx /v2/messages/whatsapp."""
    if media_url:
        media: dict[str, Any] = {"link": media_url}
        if text:
            media["caption"] = text
        return {"type": "image", "image": media}
    return {
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }


def send_whatsapp(
    db: Session, to: str, text: str, *, media_url: str | None = None
) -> dict[str, Any]:
    """Send a WhatsApp message (text, optionally with a media attachment) via Telnyx."""
    if not runtime_config.whatsapp_enabled(db):
        logger.info("[telnyx] WhatsApp not configured; skip send to %s", to)
        return {"ok": False, "skipped": True, "reason": "telnyx_not_configured"}

    api_key = runtime_config.get(db, "telnyx_api_key")
    wa_from = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    to = normalize_e164(to)

    payload: dict[str, Any] = {
        "from": wa_from,
        "to": to,
        "whatsapp_message": _whatsapp_payload(text, media_url=media_url),
    }

    profile_id: str | None = None
    try:
        with httpx.Client(timeout=15.0) as client:
            from app.services.telnyx_resolve import effective_profile_id

            profile_id, _warnings = effective_profile_id(
                client,
                api_key,
                configured_profile=runtime_config.get(db, "telnyx_messaging_profile_id"),
                from_number=wa_from,
            )
            if profile_id:
                payload["messaging_profile_id"] = profile_id
    except httpx.HTTPError:
        pass

    def _post(current: dict[str, Any]) -> httpx.Response:
        with httpx.Client(timeout=30.0) as client:
            return client.post(
                TELNYX_WHATSAPP_MESSAGES_URL, json=current, headers=_headers(api_key)
            )

    try:
        resp = _post(payload)
        ok = resp.status_code < 300
        detail = resp.text[:500] if not ok else ""
        if not ok and profile_id:
            retry = {k: v for k, v in payload.items() if k != "messaging_profile_id"}
            logger.info("[telnyx] retrying send without messaging_profile_id")
            resp2 = _post(retry)
            if resp2.status_code < 300:
                return {"ok": True, "status": resp2.status_code, "retried_without_profile": True}
            detail = resp2.text[:500]
            resp = resp2
            ok = False
        if not ok:
            logger.warning("[telnyx] send failed %s: %s", resp.status_code, detail)
        return {"ok": ok, "status": resp.status_code, "detail": detail}
    except httpx.HTTPError as exc:
        logger.warning("[telnyx] send error: %s", exc)
        return {"ok": False, "error": str(exc)}
