from __future__ import annotations

from typing import Any, Callable

from app.agent.tools import (
    escalate,
    esim_status,
    kb_search,
    order_lookup,
    refund_ticket,
    resend_qr,
)
from app.agent.tools.context import ToolContext
from app.core.logging import get_logger

logger = get_logger(__name__)

# Ordered so schemas are stable for the model.
_MODULES = [kb_search, order_lookup, esim_status, resend_qr, escalate, refund_ticket]

TOOL_SCHEMAS: list[dict[str, Any]] = [m.SCHEMA for m in _MODULES]

_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    m.SCHEMA["function"]["name"]: m.run for m in _MODULES
}


def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return fn(ctx, **(args or {}))
    except TypeError as exc:
        logger.warning("tool %s bad args %s: %s", name, args, exc)
        return {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # never crash the agent loop on a tool error
        logger.exception("tool %s failed", name)
        return {"error": f"{name} failed: {exc}"}


__all__ = ["TOOL_SCHEMAS", "execute_tool", "ToolContext"]
