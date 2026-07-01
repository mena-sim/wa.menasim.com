from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext
from app.services.providers.woocommerce import WooCommerceClient

SCHEMA = {
    "type": "function",
    "function": {
        "name": "order_lookup",
        "description": (
            "Look up a customer's menasim order(s) in WooCommerce by email, order "
            "number, or phone, and return order summary plus any eSIM data stored on "
            "the order (ICCID, QR/activation, status). Use when the customer refers to "
            "an order or needs their eSIM details."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Customer email on the order."},
                "order_number": {"type": "string", "description": "Order number/ID."},
                "phone": {"type": "string", "description": "Customer phone on the order."},
            },
        },
    },
}


def run(
    ctx: ToolContext,
    email: str | None = None,
    order_number: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    if not any([email, order_number, phone]):
        return {"found": False, "message": "Provide an email, order number, or phone to search."}

    wc = WooCommerceClient()
    if not wc.enabled:
        return {
            "found": False,
            "message": "WooCommerce is not configured yet. Ask the customer for their order "
            "number/email and escalate if you cannot proceed.",
        }

    orders = wc.find_orders(email=email, order_number=order_number, phone=phone)
    if not orders:
        return {"found": False, "message": "No matching order found. Double-check the details."}

    out = []
    for order in orders[:5]:
        summary = wc.summarize_order(order)
        summary["esim"] = wc.extract_esim(order)
        out.append(summary)
    return {"found": True, "orders": out}
