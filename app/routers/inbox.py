from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.ticket import Ticket

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


@router.get("/tickets")
def list_tickets(status: str = "open", db: Session = Depends(get_db)) -> dict:
    stmt = select(Ticket).order_by(Ticket.id.desc())
    if status and status != "all":
        stmt = stmt.where(Ticket.status == status)
    tickets = db.execute(stmt.limit(200)).scalars().all()
    out = []
    for t in tickets:
        convo = db.get(Conversation, t.conversation_id)
        out.append(
            {
                "id": t.id,
                "kind": t.kind,
                "status": t.status,
                "subject": t.subject,
                "details": t.details,
                "contact": t.contact,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "conversation_id": t.conversation_id,
                "channel": convo.channel if convo else None,
                "sender_id": convo.sender_id if convo else None,
            }
        )
    return {"tickets": out}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        return {"found": False}
    msgs = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        .scalars()
        .all()
    )
    return {
        "found": True,
        "conversation": {
            "id": convo.id,
            "channel": convo.channel,
            "sender_id": convo.sender_id,
            "language": convo.language,
            "needs_human": convo.needs_human,
        },
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "media_url": m.media_url,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }


@router.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: int, db: Session = Depends(get_db)) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return {"ok": False, "message": "Ticket not found"}
    ticket.status = "resolved"
    convo = db.get(Conversation, ticket.conversation_id)
    if convo:
        open_left = db.execute(
            select(Ticket).where(
                Ticket.conversation_id == convo.id,
                Ticket.status == "open",
                Ticket.id != ticket.id,
            )
        ).first()
        if not open_left:
            convo.needs_human = False
    db.commit()
    return {"ok": True}
