from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import runtime_config
from app.services.channels.base import InboundMessage
from app.services.telnyx_client import normalize_e164
from app.services.whatsapp.providers import whatsapp_from_number

logger = get_logger(__name__)

WHATSAPP_CHANNEL = "whatsapp"
SMS_CHANNEL = "sms"
_STATUS_CALLBACKS = frozenset(
    {"queued", "sending", "sent", "delivered", "undelivered", "failed", "read"}
)


class TwilioWebhookVerificationError(ValueError):
    pass


def _auth(db: Session) -> tuple[str, str]:
    return (
        runtime_config.get(db, "twilio_account_sid"),
        runtime_config.get(db, "twilio_auth_token"),
    )


def sms_webhook_url() -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/twilio/webhooks/sms"


def whatsapp_webhook_url() -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/twilio/webhooks/whatsapp"


def _from_address(db: Session) -> str:
    number = normalize_e164(whatsapp_from_number(db, "twilio"))
    return f"whatsapp:{number}" if number else ""


def _sms_from_address(db: Session) -> str:
    return normalize_e164(runtime_config.sms_from_number(db))


def detect_channel(form: dict[str, Any]) -> str:
    from_raw = str(form.get("From") or "").strip().lower()
    to_raw = str(form.get("To") or "").strip().lower()
    if from_raw.startswith("whatsapp:") or to_raw.startswith("whatsapp:"):
        return WHATSAPP_CHANNEL
    return SMS_CHANNEL


def is_status_callback(form: dict[str, Any]) -> bool:
    status = str(form.get("MessageStatus") or "").strip().lower()
    return status in _STATUS_CALLBACKS


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


def parse_inbound(form: dict[str, Any], *, channel: str | None = None) -> InboundMessage | None:
    if is_status_callback(form):
        return None

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

    if not body and not media_url:
        return None

    resolved = channel or detect_channel(form)
    event_id = str(form.get("MessageSid") or form.get("SmsSid") or "")
    return InboundMessage(
        channel=resolved,
        sender_id=sender,
        text=body,
        media_url=media_url,
        is_image=is_image,
        is_audio=is_audio,
        is_unsupported_media=bool(media_url and not is_image and not is_audio),
        media_content_type=content_type or None,
        event_id=event_id or None,
        parse_debug={"provider": "twilio", "channel": resolved, "raw_keys": sorted(form.keys())},
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

    return _post_message(account_sid, auth_token, data, provider="twilio")


def send_sms(
    db: Session, to: str, text: str, *, media_url: str | None = None
) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    sms_from = _sms_from_address(db)
    to_addr = normalize_e164(to)
    if not account_sid or not auth_token or not sms_from or not to_addr:
        return {"ok": False, "skipped": True, "reason": "twilio_sms_not_configured"}

    data: dict[str, str] = {"From": sms_from, "To": to_addr}
    if media_url:
        data["MediaUrl"] = media_url
        if text:
            data["Body"] = text
    else:
        data["Body"] = text

    return _post_message(account_sid, auth_token, data, provider="twilio-sms")


def _post_message(
    account_sid: str, auth_token: str, data: dict[str, str], *, provider: str
) -> dict[str, Any]:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, data=data, auth=(account_sid, auth_token))
        ok = resp.status_code < 300
        detail = resp.text[:500] if not ok else ""
        if not ok:
            logger.warning("[%s] send failed %s: %s", provider, resp.status_code, detail)
        return {"ok": ok, "status": resp.status_code, "detail": detail, "provider": provider}
    except httpx.HTTPError as exc:
        logger.warning("[%s] send error: %s", provider, exc)
        return {"ok": False, "error": str(exc), "provider": provider}


def _capability(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes")


def list_incoming_numbers(db: Session) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Twilio Account SID and Auth Token are required.", "numbers": []}

    expected_sms = sms_webhook_url()
    numbers: list[dict[str, Any]] = []
    url: str | None = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    )
    try:
        with httpx.Client(timeout=20.0) as client:
            params: dict[str, Any] | None = {"PageSize": 50}
            while url:
                resp = client.get(url, auth=(account_sid, auth_token), params=params)
                params = None
                if resp.status_code >= 300:
                    return {
                        "ok": False,
                        "message": f"HTTP {resp.status_code}: could not list Twilio numbers.",
                        "numbers": [],
                    }
                payload = resp.json()
                for item in payload.get("incoming_phone_numbers") or []:
                    phone = str(item.get("phone_number") or "")
                    sms_url = str(item.get("sms_url") or "")
                    caps = item.get("capabilities") or {}
                    numbers.append(
                        {
                            "sid": item.get("sid") or "",
                            "phone_number": phone,
                            "friendly_name": item.get("friendly_name") or phone,
                            "sms_url": sms_url,
                            "sms_configured": bool(sms_url) and sms_url.rstrip("/") == expected_sms.rstrip("/"),
                            "capabilities": {
                                "sms": _capability(caps.get("sms")),
                                "mms": _capability(caps.get("mms")),
                                "voice": _capability(caps.get("voice")),
                            },
                        }
                    )
                next_uri = payload.get("next_page_uri") or ""
                if not next_uri:
                    url = None
                elif str(next_uri).startswith("http"):
                    url = str(next_uri)
                else:
                    url = f"https://api.twilio.com{next_uri}"
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}", "numbers": []}

    return {
        "ok": True,
        "message": f"Found {len(numbers)} Twilio number(s).",
        "numbers": numbers,
        "sms_webhook_url": expected_sms,
    }


def list_recent_sms(db: Session, *, to_number: str = "", limit: int = 25) -> dict[str, Any]:
    """Read inbound/outbound SMS from Twilio's message log (includes Meta verification codes)."""
    account_sid, auth_token = _auth(db)
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Twilio Account SID and Auth Token are required.", "messages": []}

    to_number = normalize_e164(to_number or runtime_config.sms_from_number(db))
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    params: dict[str, Any] = {"PageSize": max(1, min(int(limit), 50))}
    if to_number:
        params["To"] = to_number
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, auth=(account_sid, auth_token), params=params)
        if resp.status_code >= 300:
            return {
                "ok": False,
                "message": f"HTTP {resp.status_code}: could not load Twilio SMS log.",
                "messages": [],
            }
        payload = resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}", "messages": []}

    messages = []
    for item in payload.get("messages") or []:
        body = str(item.get("body") or "")
        direction = str(item.get("direction") or "")
        messages.append(
            {
                "sid": item.get("sid") or "",
                "from": item.get("from") or "",
                "to": item.get("to") or "",
                "body": body,
                "status": item.get("status") or "",
                "direction": direction,
                "inbound": direction.startswith("inbound"),
                "date_sent": item.get("date_sent") or item.get("date_created") or "",
            }
        )
    inbound = sum(1 for m in messages if m["inbound"])
    hint = ""
    if not messages:
        hint = (
            "No SMS in Twilio's log for this number. If Meta just sent a code, wait a few seconds and retry. "
            "Twilio trial accounts only receive SMS from verified caller IDs — upgrade the Twilio account "
            "or Meta's verification SMS will never arrive."
        )
    return {
        "ok": True,
        "message": (
            f"Loaded {len(messages)} recent SMS"
            + (f" to {to_number}" if to_number else "")
            + (f" ({inbound} inbound)." if messages else ".")
        ),
        "hint": hint,
        "to": to_number,
        "messages": messages,
    }


def configure_sms_webhook(
    db: Session, *, sid: str = "", phone_number: str = ""
) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Twilio Account SID and Auth Token are required."}

    listed = list_incoming_numbers(db)
    if not listed.get("ok"):
        return listed

    target = None
    want_sid = (sid or "").strip()
    want_num = normalize_e164(phone_number) if phone_number else ""
    for item in listed.get("numbers") or []:
        if want_sid and item.get("sid") == want_sid:
            target = item
            break
        if want_num and normalize_e164(item.get("phone_number") or "") == want_num:
            target = item
            break
    if target is None and not want_sid and not want_num and len(listed.get("numbers") or []) == 1:
        target = listed["numbers"][0]
    if target is None:
        return {"ok": False, "message": "Pick a Twilio number to receive SMS."}

    if not (target.get("capabilities") or {}).get("sms"):
        return {
            "ok": False,
            "message": f"{target.get('phone_number')} cannot receive SMS. Buy or enable SMS on this number in Twilio.",
        }

    webhook = sms_webhook_url()
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/IncomingPhoneNumbers/{target['sid']}.json"
    )
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                data={"SmsUrl": webhook, "SmsMethod": "POST"},
                auth=(account_sid, auth_token),
            )
        if resp.status_code >= 300:
            return {
                "ok": False,
                "message": (
                    f"Twilio rejected webhook update (HTTP {resp.status_code}). "
                    "If this number is in a Messaging Service, set the service inbound URL "
                    f"to {webhook} instead."
                ),
                "detail": resp.text[:400],
            }
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}

    runtime_config.set_value(db, "twilio_sms_from", normalize_e164(target["phone_number"]))
    runtime_config.set_value(db, "twilio_sms_enabled", "true")
    db.commit()
    return {
        "ok": True,
        "message": f"Inbound SMS webhook set on {target['phone_number']}. Send an SMS to that number to test.",
        "phone_number": target["phone_number"],
        "sid": target["sid"],
        "sms_url": webhook,
    }


def test_connection(db: Session) -> dict[str, Any]:
    account_sid, auth_token = _auth(db)
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Twilio Account SID and Auth Token are required."}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
                auth=(account_sid, auth_token),
            )
        if resp.status_code >= 300:
            return {"ok": False, "message": f"HTTP {resp.status_code}: check Twilio credentials."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}

    details: list[str] = ["Twilio credentials are valid."]
    if _from_address(db):
        details.append(f"WhatsApp from {_from_address(db)}")
    else:
        details.append("WhatsApp sender number is not set.")
    if runtime_config.sms_enabled(db):
        details.append(f"SMS inbound enabled on {_sms_from_address(db)}")
    elif runtime_config.get_bool(db, "twilio_sms_enabled"):
        details.append("SMS is enabled but no SMS number is set.")
    else:
        details.append("SMS inbound is off — enable it and pick a number to receive texts.")
    return {"ok": True, "message": details[0], "details": details[1:]}


def test_sms_send(db: Session, to: str, text: str) -> dict[str, Any]:
    to = normalize_e164(to)
    if not to:
        return {"ok": False, "message": "Enter a destination phone number (E.164)."}
    if not runtime_config.sms_enabled(db):
        return {
            "ok": False,
            "message": "SMS is not enabled. Save Account SID, Auth Token, SMS number, and turn SMS on.",
        }
    result = send_sms(db, to, text)
    if result.get("ok"):
        return {"ok": True, "message": f"Test SMS sent to {to}.", "send": result}
    detail = result.get("detail") or result.get("error") or f"HTTP {result.get('status')}"
    return {"ok": False, "message": f"SMS send failed: {detail}", "send": result}
