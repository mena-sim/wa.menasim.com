from __future__ import annotations

from typing import Any

from app.agent.tools import identity
from app.agent.tools.context import ToolContext
from app.services.providers.woocommerce import WooCommerceClient

SCHEMA = {
    "type": "function",
    "function": {
        "name": "order_lookup",
        "description": (
            "Verify a customer's identity and read their menasim order + eSIM data. "
            "You MUST pass BOTH order_number AND email; the order is only returned when "
            "the email matches that order (identity check). Returns order summary plus "
            "eSIM data (ICCID, QR/activation, status). Use before sharing any order/eSIM "
            "details or resending a QR."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address used on the order."},
                "order_number": {"type": "string", "description": "Order number/ID."},
            },
            "required": ["email", "order_number"],
        },
    },
}


def run(
    ctx: ToolContext,
    email: str | None = None,
    order_number: str | None = None,
) -> dict[str, Any]:
    # Two-factor identity check: BOTH order number and email are required.
    if not (email and email.strip() and order_number and str(order_number).strip()):
        return {
            "verified": False,
            "found": False,
            "message": "To confirm identity I need BOTH the order number AND the email used on "
            "the order. Ask the customer for whichever is missing.",
        }

    wc = WooCommerceClient(ctx.db)
    if not wc.enabled:
        return {
            "verified": False,
            "found": False,
            "message": "WooCommerce is not configured yet. Ask the customer for their order "
            "number and email, and escalate if you cannot proceed.",
        }

    orders = wc.find_orders(email=email, order_number=order_number)
    email_norm = email.strip().lower()
    matched = None
    for order in orders:
        summary = wc.summarize_order(order)
        if (summary.get("email") or "").strip().lower() == email_norm:
            matched = order
            break

    if matched is None:
        return {
            "verified": False,
            "found": False,
            "message": "That order number and email don't match. Ask the customer to double-check "
            "the order number and the exact email used on the order.",
        }

    identity.mark_verified(ctx, order_number)
    summary = wc.summarize_order(matched)
    summary["esim"] = wc.extract_esim(matched)
    return {"verified": True, "found": True, "orders": [summary]}
