from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext
from app.services.providers import registry
from app.services.providers.woocommerce import WooCommerceClient
from app.services.qr import generate_qr_image

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


def _qr_from_woocommerce(order_number: str | None, email: str | None) -> tuple[str | None, str | None]:
    wc = WooCommerceClient()
    if not wc.enabled or not (order_number or email):
        return None, None
    orders = wc.find_orders(email=email, order_number=order_number)
    for order in orders:
        esim = wc.extract_esim(order)
        if esim.get("qr"):
            return esim["qr"], esim.get("iccid")
    return None, None


def run(
    ctx: ToolContext,
    iccid: str | None = None,
    order_ref: str | None = None,
    provider: str | None = None,
    order_number: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
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
        qr_payload, wc_iccid = _qr_from_woocommerce(order_number, email)
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
