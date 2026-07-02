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
                "amount": {"type": "number", "description": "Refund amount in USD, if known."},
            },
            "required": ["reason"],
        },
    },
}

# Guardrail: refunds at or above this value must be escalated to a human, never
# presented as agent-resolvable.
_REFUND_ESCALATION_CAP = 50.0


def run(
    ctx: ToolContext,
    reason: str,
    order_number: str | None = None,
    contact: str | None = None,
    amount: float | None = None,
) -> dict[str, Any]:
    try:
        amount_val = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_val = None
    escalated = amount_val is not None and amount_val >= _REFUND_ESCALATION_CAP

    subject = f"Refund/cancellation for order {order_number or '(unknown)'}"
    if escalated:
        subject = f"[ESCALATED >=${_REFUND_ESCALATION_CAP:.0f}] " + subject
    ticket = Ticket(
        conversation_id=ctx.conversation.id,
        kind="refund_escalation" if escalated else "refund",
        status="open",
        subject=subject[:255],
        details=(f"Amount: {amount_val}\n" if amount_val is not None else "") + reason,
        contact=contact,
    )
    ctx.db.add(ticket)
    ctx.conversation.needs_human = True
    ctx.db.commit()
    ctx.db.refresh(ticket)
    ctx.escalated = True

    send_escalation_email(
        ctx.db,
        subject=f"Refund request #{ticket.id}" + (" (ESCALATED)" if escalated else ""),
        body=(
            f"Conversation: {ctx.conversation.id} ({ctx.conversation.channel}/"
            f"{ctx.conversation.sender_id})\n"
            f"Order: {order_number or '-'}\n"
            f"Amount: {amount_val if amount_val is not None else '-'}\n"
            f"Contact: {contact or '-'}\n"
            f"Reason: {reason}\n"
        ),
    )
    if escalated:
        message = (
            f"This refund (>= ${_REFUND_ESCALATION_CAP:.0f}) is above what I can handle, so I've "
            "escalated it to a human agent. Reassure the customer that the team will review it."
        )
    else:
        message = (
            "Refund/cancellation request logged for the billing team. Do NOT promise a refund "
            "outcome; explain the policy and that the team will review."
        )
    return {
        "logged": True,
        "ticket_id": ticket.id,
        "escalated": escalated,
        "amount": amount_val,
        "message": message,
    }
