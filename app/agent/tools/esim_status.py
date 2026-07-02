from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agent.tools import identity
from app.agent.tools.context import ToolContext
from app.services.providers import registry

SCHEMA = {
    "type": "function",
    "function": {
        "name": "esim_status",
        "description": (
            "Get live eSIM status and data balance from the eSIM provider by ICCID "
            "(or order reference). Use when the customer asks about activation status, "
            "remaining data, or expiry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "iccid": {"type": "string", "description": "The eSIM ICCID."},
                "order_ref": {"type": "string", "description": "Provider order reference, if known."},
                "provider": {
                    "type": "string",
                    "description": "Provider name (airalo, esimcard, esimaccess). Omit to use the default.",
                },
            },
        },
    },
}


def run(
    ctx: ToolContext,
    iccid: str | None = None,
    order_ref: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    if not identity.is_verified(ctx):
        return identity.unverified_result()
    if not (iccid or order_ref):
        return {"found": False, "message": "Provide an ICCID or order reference to check status."}

    prov = registry.get_provider(ctx.db, name=provider)
    if prov is None:
        return {
            "found": False,
            "message": "No eSIM provider is configured/enabled. Configure one in settings, "
            "or use order_lookup to read eSIM data from WooCommerce.",
        }

    info = prov.get_esim(iccid=iccid, order_ref=order_ref)
    data = asdict(info)
    # Do not leak large raw payloads to the model.
    data.pop("raw", None)
    return data
