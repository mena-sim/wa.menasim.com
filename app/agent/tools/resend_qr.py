from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.agent.tools.context import ToolContext
from app.models.skill_call import SkillCall
from app.services.providers import registry
from app.services.providers.woocommerce import WooCommerceClient
from app.services.qr import generate_qr_image

_MAX_RESENDS_24H = 3

SCHEMA = {
    "type": "function",
    "function": {
        "name": "resend_qr",
        "description": (
            "Regenerate and send the customer's eSIM QR code as an image, plus the "
            "activation code. Use when the customer lost their QR or asks to resend it. "
            "Provide an ICCID/order_ref (provider) or an order_number/email (WooCommerce)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "iccid": {"type": "string"},
                "order_ref": {"type": "string"},
                "provider": {"type": "string", "description": "airalo | esimcard | esimaccess"},
                "order_number": {"type": "string", "description": "WooCommerce order number (fallback source)."},
                "email": {"type": "string", "description": "WooCommerce order email (fallback source)."},
            },
        },
    },
}


def _qr_from_woocommerce(ctx, order_number: str | None, email: str | None) -> tuple[str | None, str | None]:
    wc = WooCommerceClient(ctx.db)
    if not wc.enabled or not (order_number or email):
        return None, None
    orders = wc.find_orders(email=email, order_number=order_number)
    for order in orders:
        esim = wc.extract_esim(order)
        if esim.get("qr"):
            return esim["qr"], esim.get("iccid")
    return None, None


def _recent_resend_count(ctx: ToolContext) -> int:
    """Successful resends for this conversation in the last 24h (from the skill-call log)."""
    convo_id = getattr(ctx.conversation, "id", None)
    if convo_id is None:
        return 0
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    count = ctx.db.execute(
        select(func.count(SkillCall.id)).where(
            SkillCall.conversation_id == convo_id,
            SkillCall.skill_name == "resend_qr",
            SkillCall.created_at >= since,
            SkillCall.output_json.like('%"sent": true%'),
        )
    ).scalar_one_or_none()
    return int(count or 0)


def run(
    ctx: ToolContext,
    iccid: str | None = None,
    order_ref: str | None = None,
    provider: str | None = None,
    order_number: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    # Guardrail: cap resends per conversation per 24h; the 4th auto-escalates.
    if _recent_resend_count(ctx) >= _MAX_RESENDS_24H:
        from app.agent.tools import escalate as escalate_tool

        result = escalate_tool.run(
            ctx,
            reason="QR resend limit reached (>3 in 24h)",
            summary=(
                "Customer requested another eSIM QR resend after 3+ resends in 24h. "
                "Possible activation problem needing human help."
            ),
            contact=email,
        )
        result["message"] = (
            "You have already resent the QR several times recently, so I've escalated this "
            "to a human agent to look into the activation properly."
        )
        return result

    qr_payload: str | None = None
    resolved_iccid = iccid

    if iccid or order_ref:
        prov = registry.get_provider(ctx.db, name=provider)
        if prov is not None:
            info = prov.get_esim(iccid=iccid, order_ref=order_ref)
            if info.found:
                qr_payload = info.qr_code or info.activation_code
                resolved_iccid = info.iccid or iccid

    if not qr_payload:
        qr_payload, wc_iccid = _qr_from_woocommerce(ctx, order_number, email)
        resolved_iccid = resolved_iccid or wc_iccid

    if not qr_payload:
        return {
            "sent": False,
            "message": "Could not find a QR/activation code for that eSIM. Ask for the order "
            "number or email, or escalate to a human to resend it.",
        }

    image_url = generate_qr_image(qr_payload)
    ctx.media_url = image_url  # channel will attach/render this image
    return {
        "sent": True,
        "iccid": resolved_iccid,
        "qr_image_url": image_url,
        "activation_code": qr_payload,
        "message": "QR image generated and attached. Tell the customer to scan it via "
        "Settings > Add eSIM, and share the activation code as a manual fallback.",
    }
