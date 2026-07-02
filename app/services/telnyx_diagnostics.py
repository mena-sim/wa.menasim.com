from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.config import get_settings
from app.models.conversation import Conversation
from app.models.processed_event import ProcessedEvent
from app.services import runtime_config
from app.services.channels.whatsapp_telnyx_channel import WHATSAPP_CHANNEL, parse_inbound
from app.services.telnyx_client import normalize_e164, send_whatsapp

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def webhook_url() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/telnyx/webhooks/messages"


def _looks_like_business_account_id(value: str) -> bool:
    v = (value or "").strip()
    return v.isdigit() and len(v) >= 10


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def status(db: Session) -> dict[str, Any]:
    profile_id = runtime_config.get(db, "telnyx_messaging_profile_id")
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    business_id = runtime_config.get(db, "whatsapp_business_id")
    warnings: list[str] = []

    if profile_id and _looks_like_business_account_id(profile_id):
        warnings.append(
            "Messaging profile ID looks like a WhatsApp Business account ID. "
            "Use the Telnyx messaging profile UUID (e.g. 4001c123-...), not the Meta business ID."
        )
    if business_id and profile_id and business_id.strip() == profile_id.strip():
        warnings.append(
            "Messaging profile ID matches the business account ID — these are different values in Telnyx."
        )
    if not from_number:
        warnings.append("WhatsApp sender number is not set.")
    elif not from_number.startswith("+"):
        warnings.append("WhatsApp sender number should be E.164 with a leading +.")

    wa_convos = (
        db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.channel == WHATSAPP_CHANNEL)
        ).scalar_one()
        or 0
    )

    return {
        "webhook_url": webhook_url(),
        "whatsapp_enabled": runtime_config.whatsapp_enabled(db),
        "llm_enabled": runtime_config.llm_enabled(db),
        "from_number": from_number,
        "messaging_profile_id": profile_id,
        "business_account_id": business_id,
        "webhook_public_key_set": bool(runtime_config.get(db, "telnyx_webhook_public_key")),
        "whatsapp_conversations": int(wa_convos),
        "processed_webhook_events": int(
            db.execute(select(func.count()).select_from(ProcessedEvent)).scalar_one() or 0
        ),
        "warnings": warnings,
    }


def test_connection(db: Session) -> dict[str, Any]:
    """Validate Telnyx API key, messaging profile, webhook URL, and sender number."""
    api_key = runtime_config.get(db, "telnyx_api_key")
    if not api_key:
        return {"ok": False, "message": "Telnyx API key is not set."}

    profile_id = (runtime_config.get(db, "telnyx_messaging_profile_id") or "").strip()
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    expected_webhook = webhook_url()
    details: list[str] = []
    warnings: list[str] = []

    if not from_number:
        return {"ok": False, "message": "WhatsApp sender number is not set (Settings → WhatsApp)."}
    details.append(f"Sender: {from_number}")

    if profile_id and _looks_like_business_account_id(profile_id):
        return {
            "ok": False,
            "message": (
                f"'{profile_id}' looks like a WhatsApp Business account ID. "
                "Paste the Telnyx messaging profile UUID in Settings → Telnyx instead."
            ),
        }

    try:
        with httpx.Client(timeout=20.0) as client:
            ping = client.get(
                "https://api.telnyx.com/v2/messaging_profiles",
                params={"page[size]": 1},
                headers=_headers(api_key),
            )
            if ping.status_code >= 300:
                return {
                    "ok": False,
                    "message": f"Telnyx API key rejected (HTTP {ping.status_code}).",
                }

            if profile_id:
                prof = client.get(
                    f"https://api.telnyx.com/v2/messaging_profiles/{profile_id}",
                    headers=_headers(api_key),
                )
                if prof.status_code == 404:
                    return {
                        "ok": False,
                        "message": f"Messaging profile '{profile_id}' was not found in Telnyx.",
                    }
                if prof.status_code >= 300:
                    return {
                        "ok": False,
                        "message": f"Could not load messaging profile (HTTP {prof.status_code}).",
                    }
                pdata = prof.json().get("data") or {}
                portal_webhook = (pdata.get("webhook_url") or "").strip()
                details.append(f"Profile: {pdata.get('name') or profile_id}")
                if portal_webhook:
                    details.append(f"Telnyx webhook: {portal_webhook}")
                    if portal_webhook.rstrip("/") != expected_webhook.rstrip("/"):
                        warnings.append(
                            f"Telnyx profile webhook is '{portal_webhook}' but this app expects "
                            f"'{expected_webhook}'."
                        )
                else:
                    warnings.append("No webhook URL is set on the Telnyx messaging profile.")

                nums = client.get(
                    "https://api.telnyx.com/v2/phone_numbers",
                    params={
                        "filter[messaging_profile_id]": profile_id,
                        "page[size]": 50,
                    },
                    headers=_headers(api_key),
                )
                if nums.status_code < 300:
                    numbers = [
                        normalize_e164(str((row or {}).get("phone_number") or ""))
                        for row in (nums.json().get("data") or [])
                    ]
                    numbers = [n for n in numbers if n]
                    if numbers:
                        details.append("Numbers on profile: " + ", ".join(numbers))
                        if from_number not in numbers:
                            warnings.append(
                                f"Sender {from_number} is not listed on messaging profile {profile_id}."
                            )
                    else:
                        warnings.append("No phone numbers are assigned to this messaging profile.")
            else:
                warnings.append("Messaging profile ID is not set — recommended for WhatsApp routing.")

    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}

    msg = "Telnyx WhatsApp configuration looks good."
    if warnings:
        msg = "Connected, but check the warnings below."
    return {
        "ok": True,
        "message": msg,
        "details": details,
        "warnings": warnings,
        "webhook_url": expected_webhook,
    }


def test_send(db: Session, to: str, text: str) -> dict[str, Any]:
    to = normalize_e164(to)
    if not to:
        return {"ok": False, "message": "Enter a destination phone number (E.164)."}
    result = send_whatsapp(db, to, text)
    if result.get("ok"):
        return {
            "ok": True,
            "message": f"Test message sent to {to}.",
            "send": result,
        }
    detail = result.get("detail") or result.get("error") or f"HTTP {result.get('status')}"
    return {
        "ok": False,
        "message": f"Send failed: {detail}",
        "send": result,
    }


def test_inbound(
    db: Session,
    *,
    from_number: str,
    text: str,
    send_reply: bool = False,
) -> dict[str, Any]:
    """Run the same agent pipeline used for real WhatsApp webhooks."""
    from_number = normalize_e164(from_number)
    if not from_number:
        return {"ok": False, "message": "Enter the customer phone number to simulate."}

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": f"admin-test-{from_number}",
                "type": "whatsapp",
                "from": from_number,
                "text": text,
            },
        }
    }
    inbound = parse_inbound(body)
    if inbound is None:
        return {"ok": False, "message": "Could not parse simulated Telnyx webhook payload."}

    result = handle_message(
        db,
        channel=WHATSAPP_CHANNEL,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
    )
    out: dict[str, Any] = {
        "ok": True,
        "message": "Agent processed the message on the WhatsApp channel.",
        "conversation_id": result.conversation_id,
        "reply": result.reply,
        "suppressed": result.suppressed,
        "escalated": result.escalated,
        "channel": WHATSAPP_CHANNEL,
        "sender_id": from_number,
    }
    if send_reply and result.reply and not result.suppressed:
        send_result = send_whatsapp(db, from_number, result.reply, media_url=result.media_url)
        out["send"] = send_result
        out["ok"] = bool(send_result.get("ok"))
        if not send_result.get("ok"):
            out["message"] = "Agent replied, but WhatsApp delivery failed."
    return out
