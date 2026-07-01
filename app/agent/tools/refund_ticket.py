from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext
from app.models.ticket import Ticket
from app.services.notify import send_escalation_email

SCHEMA = {
    "type": "function",
    "function": {
        "name": "refund_ticket",
        "description": (
            "Log a refund or cancellation request for the billing team to review. "
            "The agent CANNOT issue refunds automatically - this only creates a ticket. "
            "Follow the refund policy from the knowledge base before promising anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "Order number to refund/cancel."},
                "reason": {"type": "string", "description": "Customer's reason for the refund/cancellation."},
                "contact": {"type": "string", "description": "Customer contact (email/phone)."},
            },
            "required": ["reason"],
        },
    },
}


def run(
    ctx: ToolContext,
    reason: str,
    order_number: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    subject = f"Refund/cancellation for order {order_number or '(unknown)'}"
    ticket = Ticket(
        conversation_id=ctx.conversation.id,
        kind="refund",
        status="open",
        subject=subject[:255],
        details=reason,
        contact=contact,
    )
    ctx.db.add(ticket)
    ctx.conversation.needs_human = True
    ctx.db.commit()
    ctx.db.refresh(ticket)
    ctx.escalated = True

    send_escalation_email(
        subject=f"Refund request #{ticket.id}",
        body=(
            f"Conversation: {ctx.conversation.id} ({ctx.conversation.channel}/"
            f"{ctx.conversation.sender_id})\n"
            f"Order: {order_number or '-'}\n"
            f"Contact: {contact or '-'}\n"
            f"Reason: {reason}\n"
        ),
    )
    return {
        "logged": True,
        "ticket_id": ticket.id,
        "message": "Refund/cancellation request logged for the billing team. Do NOT promise "
        "a refund outcome; explain the policy and that the team will review.",
    }
