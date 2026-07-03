from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.processed_event import ProcessedEvent
from app.routers.telnyx_webhook import _is_duplicate, _mark_processed
from app.services import webhook_log
from app.services.channels.whatsapp_telnyx_channel import process_inbound
from app.services.whatsapp.twilio_provider import (
    TwilioWebhookVerificationError,
    parse_inbound,
    verify_webhook,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/twilio/webhooks", tags=["twilio"])


@router.post("/whatsapp")
async def twilio_whatsapp(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = f"{get_settings().public_base_url.rstrip('/')}/twilio/webhooks/whatsapp"

    try:
        verify_webhook(
            db,
            url=url,
            params=params,
            signature_header=request.headers.get("X-Twilio-Signature"),
        )
    except TwilioWebhookVerificationError as exc:
        logger.warning("twilio webhook rejected: %s", exc)
        webhook_log.record(db, event_type="twilio.whatsapp", status="rejected", detail=str(exc))
        return JSONResponse(status_code=401, content={"error": str(exc)})

    inbound = parse_inbound(params)
    if inbound is None:
        webhook_log.record(db, event_type="twilio.whatsapp", status="ignored", detail=str(params)[:500])
        return JSONResponse(status_code=200, content={"ignored": True})

    if inbound.event_id and _is_duplicate(db, inbound.event_id):
        webhook_log.record(
            db,
            event_type="twilio.whatsapp",
            event_id=inbound.event_id,
            sender=inbound.sender_id,
            status="duplicate",
        )
        return JSONResponse(status_code=200, content={"duplicate": True})

    try:
        result = process_inbound(db, inbound)
    except Exception:
        logger.exception("twilio inbound processing failed from=%s", inbound.sender_id)
        webhook_log.record(
            db,
            event_type="twilio.whatsapp",
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
        event_type="twilio.whatsapp",
        event_id=inbound.event_id or "",
        sender=inbound.sender_id,
        status="replied" if result.get("replied") else "no_reply",
        detail=detail,
    )
    return JSONResponse(status_code=200, content=result)
