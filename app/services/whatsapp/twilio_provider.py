from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import normalize_e164
from app.services.whatsapp.providers import whatsapp_from_number

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"


class TwilioWebhookVerificationError(ValueError):
    pass


def _auth(db: Session) -> tuple[str, str]:
    return (
        runtime_config.get(db, "twilio_account_sid"),
        runtime_config.get(db, "twilio_auth_token"),
    )


def _from_address(db: Session) -> str:
    number = normalize_e164(whatsapp_from_number(db, "twilio"))
    return f"whatsapp:{number}" if number else ""


def verify_webhook(
    db: Session,
    *,
    url: str,
    params: dict[str, str],
    signature_header: str | None,
) -> bool:
    auth_token = _auth(db)[1]
    if not auth_token:
        logger.debug("[twilio-verify] skipped (no auth token configured)")
        return True
    if not signature_header:
        raise TwilioWebhookVerificationError("Missing X-Twilio-Signature header")

    data = url
    for key in sorted(params):
        data += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    if not hmac.compare_digest(expected, signature_header):
        raise TwilioWebhookVerificationError("Invalid Twilio webhook signature")
    return True


def parse_inbound(form: dict[str, Any]) -> InboundMessage | None:
    sender = normalize_e164(str(form.get("From") or "").replace("whatsapp:", ""))
    if not sender:
        return None

    body = str(form.get("Body") or "").strip()
    media_url = None
    content_type = ""
    is_image = False
    is_audio = False
    num_media = int(str(form.get("NumMedia") or "0") or "0")
    if num_media > 0:
        media_url = str(form.get("MediaUrl0") or "") or None
        content_type = str(form.get("MediaContentType0") or "").lower()
        if content_type.startswith("audio/"):
            is_audio = True
        elif content_type.startswith("image/"):
            is_image = True

    event_id = str(form.get("MessageSid") or form.get("SmsSid") or "")
    return InboundMessage(
        channel=WHATSAPP_CHANNEL,
        sender_id=sender,
        text=body,
        media_url=media_url,
        is_image=is_image,
        is_audio=is_audio,
        is_unsupported_media=bool(media_url and not is_image and not is_audio),
        media_content_type=content_type or None,
        event_id=event_id or None,
        parse_debug={"provider": "twilio", "raw_keys": sorted(form.keys())},
    )


def send_whatsapp(
    db: Session, to: str, text: str, *, media_url: str | None = None
) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    wa_from = _from_address(db)
    to_addr = f"whatsapp:{normalize_e164(to)}"
    if not account_sid or not auth_token or not wa_from:
        return {"ok": False, "skipped": True, "reason": "twilio_not_configured"}

    data: dict[str, str] = {"From": wa_from, "To": to_addr}
    if media_url:
        data["MediaUrl"] = media_url
        if text:
            data["Body"] = text
    else:
        data["Body"] = text

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, data=data, auth=(account_sid, auth_token))
        ok = resp.status_code < 300
        detail = resp.text[:500] if not ok else ""
        if not ok:
            logger.warning("[twilio] send failed %s: %s", resp.status_code, detail)
        return {"ok": ok, "status": resp.status_code, "detail": detail, "provider": "twilio"}
    except httpx.HTTPError as exc:
        logger.warning("[twilio] send error: %s", exc)
        return {"ok": False, "error": str(exc), "provider": "twilio"}


def test_connection(db: Session) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Twilio Account SID and Auth Token are required."}
    if not _from_address(db):
        return {"ok": False, "message": "Twilio WhatsApp sender number is not set."}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
                auth=(account_sid, auth_token),
            )
        if resp.status_code < 300:
            return {"ok": True, "message": "Twilio credentials are valid."}
        return {"ok": False, "message": f"HTTP {resp.status_code}: check Twilio credentials."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}
