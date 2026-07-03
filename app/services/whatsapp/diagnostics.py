from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.services import runtime_config
from app.services.channels.base import InboundMessage
from app.services.channels.whatsapp_telnyx_channel import WHATSAPP_CHANNEL, parse_inbound
from app.services.telnyx_client import normalize_e164
from app.services.whatsapp import active_webhook_url, send_message, webhook_urls, whatsapp_provider
from app.services.whatsapp.meta_provider import test_connection as test_meta
from app.services.whatsapp.twilio_provider import test_connection as test_twilio
from app.services import telnyx_diagnostics


def test_connection(db: Session) -> dict[str, Any]:
    provider = whatsapp_provider(db)
    if provider == "twilio":
        result = test_twilio(db)
    elif provider == "meta":
        result = test_meta(db)
    else:
        result = telnyx_diagnostics.test_connection(db)
    result["provider"] = provider
    result["webhook_url"] = active_webhook_url(db)
    return result


def test_send(db: Session, to: str, text: str) -> dict[str, Any]:
    to = normalize_e164(to)
    if not to:
        return {"ok": False, "message": "Enter a destination phone number (E.164)."}
    result = send_message(db, to, text)
    if result.get("ok"):
        return {
            "ok": True,
            "message": f"Test message sent to {to} via {whatsapp_provider(db)}.",
            "send": result,
        }
    detail = result.get("detail") or result.get("error") or f"HTTP {result.get('status')}"
    return {"ok": False, "message": f"Send failed: {detail}", "send": result}


def test_inbound(
    db: Session,
    *,
    from_number: str,
    text: str,
    send_reply: bool = False,
) -> dict[str, Any]:
    from_number = normalize_e164(from_number)
    if not from_number:
        return {"ok": False, "message": "Enter the customer phone number to simulate."}

    provider = whatsapp_provider(db)
    if provider == "twilio":
        inbound = InboundMessage(
            channel=WHATSAPP_CHANNEL,
            sender_id=from_number,
            text=text,
        )
    elif provider == "meta":
        inbound = InboundMessage(
            channel=WHATSAPP_CHANNEL,
            sender_id=from_number,
            text=text,
        )
    else:
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
        "message": f"Agent processed the message on the WhatsApp channel ({provider}).",
        "conversation_id": result.conversation_id,
        "reply": result.reply,
        "suppressed": result.suppressed,
        "escalated": result.escalated,
        "channel": WHATSAPP_CHANNEL,
        "sender_id": from_number,
        "provider": provider,
    }
    if send_reply and result.reply and not result.suppressed:
        send_result = send_message(db, from_number, result.reply, media_url=result.media_url)
        out["send"] = send_result
        out["ok"] = bool(send_result.get("ok"))
        if not send_result.get("ok"):
            out["message"] = "Agent replied, but WhatsApp delivery failed."
    return out


def status(db: Session) -> dict[str, Any]:
    provider = whatsapp_provider(db)
    base = telnyx_diagnostics.status(db) if provider == "telnyx" else {}
    urls = webhook_urls()
    return {
        **base,
        "provider": provider,
        "webhook_url": urls.get(provider, ""),
        "webhook_urls": urls,
        "whatsapp_enabled": runtime_config.whatsapp_enabled(db),
        "from_number": runtime_config.whatsapp_from_number(db),
    }
