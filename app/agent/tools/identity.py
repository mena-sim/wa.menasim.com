from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext


def is_verified(ctx: ToolContext) -> bool:
    """True once the customer has confirmed order number + matching email this conversation."""
    return bool(getattr(ctx.conversation, "verified", False))


def mark_verified(ctx: ToolContext, order_number: str | None) -> None:
    ctx.conversation.verified = True
    ctx.conversation.verified_order = str(order_number or "")[:64]
    ctx.db.commit()


def unverified_result() -> dict[str, Any]:
    """Standard payload telling the model to verify identity before sharing anything."""
    return {
        "verified": False,
        "message": (
            "Identity not verified yet. Before sharing any order or eSIM details, ask the "
            "customer for BOTH their order number AND the email used on the order, then call "
            "order_lookup with both to verify."
        ),
    }
