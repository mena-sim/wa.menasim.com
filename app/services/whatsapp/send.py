from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import runtime_config
from app.services.whatsapp.providers import whatsapp_provider


def send_message(
    db: Session, to: str, text: str, *, media_url: str | None = None
) -> dict[str, Any]:
    """Send a WhatsApp message using the configured provider."""
    if not runtime_config.whatsapp_enabled(db):
        return {"ok": False, "skipped": True, "reason": "whatsapp_not_configured"}

    provider = whatsapp_provider(db)
    if provider == "twilio":
        from app.services.whatsapp.twilio_provider import send_whatsapp as _send

        return _send(db, to, text, media_url=media_url)
    if provider == "meta":
        from app.services.whatsapp.meta_provider import send_whatsapp as _send

        return _send(db, to, text, media_url=media_url)

    from app.services.whatsapp.telnyx_provider import send_whatsapp as _send

    return _send(db, to, text, media_url=media_url)
