from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TELNYX_WHATSAPP_MESSAGES_URL = "https://api.telnyx.com/v2/messages/whatsapp"


class TelnyxWebhookVerificationError(ValueError):
    pass


def verify_webhook(
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
    public_key_b64 = (get_settings().telnyx_webhook_public_key or "").strip()
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


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().telnyx_api_key}",
        "Content-Type": "application/json",
    }


def send_whatsapp(to: str, text: str, *, media_url: str | None = None) -> dict[str, Any]:
    """Send a WhatsApp message (text, optionally with a media attachment) via Telnyx."""
    settings = get_settings()
    if not settings.whatsapp_enabled:
        logger.info("[telnyx] WhatsApp not configured; skip send to %s", to)
        return {"ok": False, "skipped": True, "reason": "telnyx_not_configured"}

    payload: dict[str, Any] = {
        "from": settings.telnyx_whatsapp_from,
        "to": to,
        "type": "text",
        "text": text,
    }
    if settings.telnyx_messaging_profile_id:
        payload["messaging_profile_id"] = settings.telnyx_messaging_profile_id
    if media_url:
        payload["type"] = "media"
        payload["media_urls"] = [media_url]
        payload["text"] = text

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(TELNYX_WHATSAPP_MESSAGES_URL, json=payload, headers=_headers())
            ok = resp.status_code < 300
            if not ok:
                logger.warning("[telnyx] send failed %s: %s", resp.status_code, resp.text[:400])
            return {"ok": ok, "status": resp.status_code}
    except httpx.HTTPError as exc:
        logger.warning("[telnyx] send error: %s", exc)
        return {"ok": False, "error": str(exc)}
