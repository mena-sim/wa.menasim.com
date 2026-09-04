from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services import runtime_config

PROVIDERS = ("telnyx", "twilio", "meta")


def whatsapp_provider(db: Session) -> str:
    return runtime_config.whatsapp_provider(db)


def whatsapp_from_number(db: Session, provider: str | None = None) -> str:
    if provider and provider != runtime_config.whatsapp_provider(db):
        if provider == "twilio":
            return runtime_config.get(db, "twilio_whatsapp_from")
        if provider == "meta":
            return runtime_config.get(db, "meta_whatsapp_from") or runtime_config.get(
                db, "telnyx_whatsapp_from"
            )
        return runtime_config.get(db, "telnyx_whatsapp_from")
    return runtime_config.whatsapp_from_number(db)


def webhook_urls() -> dict[str, str]:
    base = get_settings().public_base_url.rstrip("/")
    return {
        "telnyx": f"{base}/telnyx/webhooks/messages",
        "twilio": f"{base}/twilio/webhooks/whatsapp",
        "meta": f"{base}/meta/webhooks/whatsapp",
        "sms": f"{base}/twilio/webhooks/sms",
    }


def active_webhook_url(db: Session) -> str:
    return webhook_urls().get(whatsapp_provider(db), webhook_urls()["telnyx"])
