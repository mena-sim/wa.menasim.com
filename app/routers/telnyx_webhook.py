from __future__ import annotations

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


def _already_processed(db: Session, event_id: str) -> bool:
    exists = (
        db.query(ProcessedEvent).filter(ProcessedEvent.event_id == event_id).first()
        is not None
    )
    if exists:
        return True
    db.add(ProcessedEvent(event_id=event_id))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return True  # unique constraint hit => concurrent duplicate
    return False


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
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    inbound = parse_inbound(body)
    if inbound is None:
        return JSONResponse(status_code=200, content={"ignored": True})

    if inbound.event_id and _already_processed(db, inbound.event_id):
        logger.info("telnyx duplicate event ignored: %s", inbound.event_id)
        return JSONResponse(status_code=200, content={"duplicate": True})

    try:
        result = process_inbound(db, inbound)
    except Exception:
        logger.exception("telnyx inbound processing failed")
        return JSONResponse(status_code=200, content={"error": "processing_failed"})

    return JSONResponse(status_code=200, content=result)
