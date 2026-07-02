from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.processed_event import ProcessedEvent
from app.services.channels.whatsapp_telnyx_channel import parse_inbound, process_inbound
from app.services.telnyx_client import (
    TelnyxWebhookVerificationError,
    verify_webhook,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/telnyx/webhooks", tags=["telnyx"])


def _is_duplicate(db: Session, event_id: str) -> bool:
    return (
        db.query(ProcessedEvent).filter(ProcessedEvent.event_id == event_id).first()
        is not None
    )


def _mark_processed(db: Session, event_id: str) -> None:
    if not event_id:
        return
    if _is_duplicate(db, event_id):
        return
    db.add(ProcessedEvent(event_id=event_id))
    try:
        db.commit()
    except Exception:
        db.rollback()


@router.post("/messages")
async def telnyx_messages(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    raw = await request.body()
    try:
        verify_webhook(
            db,
            raw,
            signature_header=request.headers.get("telnyx-signature-ed25519"),
            timestamp_header=request.headers.get("telnyx-timestamp"),
        )
    except TelnyxWebhookVerificationError as exc:
        logger.warning("telnyx webhook rejected: %s", exc)
        return JSONResponse(status_code=401, content={"error": str(exc)})

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    event_type = str((body.get("data") or {}).get("event_type") or "")
    inbound = parse_inbound(body)
    if inbound is None:
        logger.debug("telnyx webhook ignored event_type=%s", event_type or "unknown")
        return JSONResponse(status_code=200, content={"ignored": True, "event_type": event_type})

    logger.info(
        "telnyx inbound from=%s text=%r event_id=%s",
        inbound.sender_id,
        (inbound.text or "")[:80],
        inbound.event_id,
    )

    if inbound.event_id and _is_duplicate(db, inbound.event_id):
        logger.info("telnyx duplicate event ignored: %s", inbound.event_id)
        return JSONResponse(status_code=200, content={"duplicate": True})

    try:
        result = process_inbound(db, inbound)
    except Exception:
        logger.exception("telnyx inbound processing failed from=%s", inbound.sender_id)
        return JSONResponse(status_code=200, content={"error": "processing_failed"})

    if inbound.event_id:
        _mark_processed(db, inbound.event_id)

    return JSONResponse(status_code=200, content=result)
