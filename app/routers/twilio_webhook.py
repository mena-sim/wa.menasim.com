from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.routers.telnyx_webhook import _is_duplicate, _mark_processed
from app.services import webhook_log
from app.services.channels.sms_channel import process_inbound as process_sms_inbound
from app.services.channels.whatsapp_telnyx_channel import process_inbound as process_whatsapp_inbound
from app.services.whatsapp.twilio_provider import (
    SMS_CHANNEL,
    WHATSAPP_CHANNEL,
    TwilioWebhookVerificationError,
    parse_inbound,
    sms_webhook_url,
    verify_webhook,
    whatsapp_webhook_url,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/twilio/webhooks", tags=["twilio"])


@router.post("/whatsapp")
async def twilio_whatsapp(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    return await _handle_twilio_inbound(
        request,
        db,
        event_type="twilio.whatsapp",
        fallback_channel=WHATSAPP_CHANNEL,
        verify_url=whatsapp_webhook_url(),
    )


@router.post("/sms")
async def twilio_sms(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    return await _handle_twilio_inbound(
        request,
        db,
        event_type="twilio.sms",
        fallback_channel=SMS_CHANNEL,
        verify_url=sms_webhook_url(),
    )


async def _handle_twilio_inbound(
    request: Request,
    db: Session,
    *,
    event_type: str,
    fallback_channel: str,
    verify_url: str,
) -> JSONResponse:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    try:
        verify_webhook(
            db,
            url=verify_url,
            params=params,
            signature_header=request.headers.get("X-Twilio-Signature"),
        )
    except TwilioWebhookVerificationError as exc:
        logger.warning("twilio webhook rejected: %s", exc)
        webhook_log.record(db, event_type=event_type, status="rejected", detail=str(exc))
        return JSONResponse(status_code=401, content={"error": str(exc)})

    inbound = parse_inbound(params, channel=None)
    if inbound is None:
        webhook_log.record(db, event_type=event_type, status="ignored", detail=str(params)[:500])
        return JSONResponse(status_code=200, content={"ignored": True})

    # Honour the actual From/To prefix so either webhook URL can receive both
    # WhatsApp and SMS. If detect_channel is ambiguous, use the endpoint default.
    if inbound.channel not in (WHATSAPP_CHANNEL, SMS_CHANNEL):
        inbound.channel = fallback_channel

    if inbound.event_id and _is_duplicate(db, inbound.event_id):
        webhook_log.record(
            db,
            event_type=event_type,
            event_id=inbound.event_id,
            sender=inbound.sender_id,
            status="duplicate",
        )
        return JSONResponse(status_code=200, content={"duplicate": True})

    try:
        if inbound.channel == SMS_CHANNEL:
            result = process_sms_inbound(db, inbound)
        else:
            result = process_whatsapp_inbound(db, inbound)
    except Exception:
        logger.exception("twilio inbound processing failed from=%s", inbound.sender_id)
        webhook_log.record(
            db,
            event_type=event_type,
            event_id=inbound.event_id or "",
            sender=inbound.sender_id,
            status="error",
            detail="processing_failed",
        )
        return JSONResponse(status_code=200, content={"error": "processing_failed"})

    if inbound.event_id:
        _mark_processed(db, inbound.event_id)

    media_log = result.get("media_log")
    detail = ""
    if media_log:
        try:
            detail = json.dumps(media_log, ensure_ascii=False)[:4000]
        except (TypeError, ValueError):
            detail = str(media_log)[:4000]

    webhook_log.record(
        db,
        event_type=event_type,
        event_id=inbound.event_id or "",
        sender=inbound.sender_id,
        status="replied" if result.get("replied") else "no_reply",
        detail=detail,
    )
    return JSONResponse(status_code=200, content=result)
