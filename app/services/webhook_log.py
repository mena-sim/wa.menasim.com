from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.webhook_event import WebhookEvent

_MAX_ROWS = 200


def record(
    db: Session,
    *,
    event_type: str = "",
    event_id: str = "",
    sender: str = "",
    status: str,
    detail: str = "",
) -> None:
    db.add(
        WebhookEvent(
            event_type=(event_type or "")[:64],
            event_id=(event_id or "")[:255],
            sender=(sender or "")[:64],
            status=(status or "")[:32],
            detail=(detail or "")[:4000],
        )
    )
    db.commit()
    _trim(db)


def _trim(db: Session) -> None:
    keep = (
        db.execute(select(WebhookEvent.id).order_by(WebhookEvent.id.desc()).limit(_MAX_ROWS))
        .scalars()
        .all()
    )
    if not keep:
        return
    db.execute(delete(WebhookEvent).where(WebhookEvent.id.not_in(keep)))
    db.commit()


def recent(db: Session, limit: int = 30) -> list[dict]:
    rows = (
        db.execute(select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "event_type": r.event_type,
            "event_id": r.event_id,
            "sender": r.sender,
            "status": r.status,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
