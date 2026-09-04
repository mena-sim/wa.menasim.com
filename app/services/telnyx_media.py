from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.services import runtime_config


def fetch_media(db: Session, url: str, *, timeout: float = 60.0) -> tuple[bytes, str]:
    """Download WhatsApp/SMS media (auth depends on active provider)."""
    from app.services.whatsapp.providers import whatsapp_provider

    headers: dict[str, str] = {}
    host = (url or "").lower()
    twilio_media = "api.twilio.com" in host or "media.twilio.com" in host
    provider = whatsapp_provider(db)
    if provider == "twilio" or twilio_media:
        sid = runtime_config.get(db, "twilio_account_sid")
        token = runtime_config.get(db, "twilio_auth_token")
        auth = (sid, token) if sid and token else None
    elif provider == "meta":
        token = runtime_config.get(db, "meta_whatsapp_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        auth = None
    else:
        api_key = (runtime_config.get(db, "telnyx_api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        auth = None

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers, auth=auth)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        return resp.content, content_type
