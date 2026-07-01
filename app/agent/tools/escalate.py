from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext
from app.models.ticket import Ticket
from app.services.notify import send_escalation_email

SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": (
            "Escalate the conversation to a human support agent. Use when you cannot "
            "resolve the issue, the customer is upset, the request is out of scope, or "
            "the customer sent an image/screenshot you cannot interpret."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short reason for escalation."},
                "summary": {"type": "string", "description": "Summary of the issue and what was tried."},
                "contact": {"type": "string", "description": "Customer contact (email/phone) if known."},
            },
            "required": ["reason"],
        },
    },
}


def run(
    ctx: ToolContext,
    reason: str,
    summary: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    ticket = Ticket(
        conversation_id=ctx.conversation.id,
        kind="escalation",
        status="open",
        subject=reason[:255],
        details=summary or reason,
        contact=contact,
    )
    ctx.db.add(ticket)
    ctx.conversation.needs_human = True
    ctx.db.commit()
    ctx.db.refresh(ticket)
    ctx.escalated = True

    send_escalation_email(
        subject=f"Escalation #{ticket.id}: {reason}",
        body=(
            f"Conversation: {ctx.conversation.id} ({ctx.conversation.channel}/"
            f"{ctx.conversation.sender_id})\n"
            f"Reason: {reason}\n"
            f"Contact: {contact or '-'}\n\n"
            f"Summary:\n{summary or reason}\n"
        ),
    )
    return {
        "escalated": True,
        "ticket_id": ticket.id,
        "message": "A human agent has been notified and will follow up. Reassure the customer.",
    }
