from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.routers.telnyx_webhook import _is_duplicate, _mark_processed
from app.services import webhook_log
from app.services.channels.whatsapp_telnyx_channel import process_inbound
from app.services.whatsapp.meta_provider import (
    MetaWebhookVerificationError,
    parse_inbound,
    verify_subscription,
    verify_webhook,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/meta/webhooks", tags=["meta"])


@router.get("/whatsapp")
def meta_whatsapp_verify(
    db: Session = Depends(get_db),
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    challenge = verify_subscription(hub_mode, hub_verify_token, hub_challenge, db)
    if challenge is None:
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(challenge)


@router.post("/whatsapp")
async def meta_whatsapp(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    raw = await request.body()
    try:
        verify_webhook(db, raw, request.headers.get("X-Hub-Signature-256"))
    except MetaWebhookVerificationError as exc:
        logger.warning("meta webhook rejected: %s", exc)
        webhook_log.record(db, event_type="meta.whatsapp", status="rejected", detail=str(exc))
        return JSONResponse(status_code=401, content={"error": str(exc)})

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        webhook_log.record(db, event_type="meta.whatsapp", status="bad_json")
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    inbounds = parse_inbound(body)
    if not inbounds:
        webhook_log.record(db, event_type="meta.whatsapp", status="ignored", detail=raw[:500].decode("utf-8", "replace"))
        return JSONResponse(status_code=200, content={"ignored": True})

    results = []
    for inbound in inbounds:
        if inbound.event_id and _is_duplicate(db, inbound.event_id):
            results.append({"duplicate": True, "event_id": inbound.event_id})
            continue
        try:
            result = process_inbound(db, inbound)
        except Exception:
            logger.exception("meta inbound processing failed from=%s", inbound.sender_id)
            webhook_log.record(
                db,
                event_type="meta.whatsapp",
                event_id=inbound.event_id or "",
                sender=inbound.sender_id,
                status="error",
                detail="processing_failed",
            )
            results.append({"error": "processing_failed", "sender": inbound.sender_id})
            continue

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
            event_type="meta.whatsapp",
            event_id=inbound.event_id or "",
            sender=inbound.sender_id,
            status="replied" if result.get("replied") else "no_reply",
            detail=detail,
        )
        results.append(result)

    return JSONResponse(status_code=200, content={"results": results})
