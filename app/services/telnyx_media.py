from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.services import runtime_config


def fetch_media(db: Session, url: str, *, timeout: float = 60.0) -> tuple[bytes, str]:
    """Download Telnyx/WhatsApp media (URLs are often auth-gated)."""
    headers: dict[str, str] = {}
    api_key = (runtime_config.get(db, "telnyx_api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        return resp.content, content_type
