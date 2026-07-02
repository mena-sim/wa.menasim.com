from __future__ import annotations

import json
from typing import Any, Callable

from app.agent.tools import (
    check_esim_usage,
    escalate,
    esim_status,
    kb_search,
    order_lookup,
    refund_ticket,
    resend_qr,
)
from app.agent.tools.context import ToolContext
from app.core.logging import get_logger
from app.models.skill_call import SkillCall

logger = get_logger(__name__)


def _log_skill_call(ctx: ToolContext, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    """Persist a skill-call audit row (SKILLS.md global rule). Never crashes the loop."""
    try:
        convo_id = getattr(getattr(ctx, "conversation", None), "id", None)
        ctx.db.add(
            SkillCall(
                conversation_id=convo_id,
                skill_name=name,
                input_json=json.dumps(args or {}, ensure_ascii=False)[:4000],
                output_json=json.dumps(result or {}, ensure_ascii=False, default=str)[:4000],
            )
        )
        ctx.db.commit()
    except Exception:  # pragma: no cover - logging must not break the agent
        logger.exception("failed to log skill_call for %s", name)
        try:
            ctx.db.rollback()
        except Exception:
            pass

# Ordered so schemas are stable for the model.
_MODULES = [
    kb_search,
    order_lookup,
    check_esim_usage,
    esim_status,
    resend_qr,
    escalate,
    refund_ticket,
]

TOOL_SCHEMAS: list[dict[str, Any]] = [m.SCHEMA for m in _MODULES]

_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    m.SCHEMA["function"]["name"]: m.run for m in _MODULES
}


def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        result = fn(ctx, **(args or {}))
    except TypeError as exc:
        logger.warning("tool %s bad args %s: %s", name, args, exc)
        result = {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # never crash the agent loop on a tool error
        logger.exception("tool %s failed", name)
        result = {"error": f"{name} failed: {exc}"}
    _log_skill_call(ctx, name, args or {}, result)
    return result


__all__ = ["TOOL_SCHEMAS", "execute_tool", "ToolContext"]
