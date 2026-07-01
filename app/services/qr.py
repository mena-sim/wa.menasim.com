from __future__ import annotations

import hashlib
import os
from pathlib import Path

import qrcode

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MEDIA_DIR = Path("data/media")


def generate_qr_image(payload: str) -> str:
    """Render `payload` as a QR PNG under data/media and return a public URL path.

    Returns a URL like {PUBLIC_BASE_URL}/media/<hash>.png that both the web UI
    (inline <img>) and WhatsApp (media message) can use.
    """
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    filename = f"qr_{digest}.png"
    path = MEDIA_DIR / filename
    if not path.exists():
        img = qrcode.make(payload)
        img.save(path)
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/media/{filename}"


def media_fs_path(filename: str) -> str:
    return os.path.join(str(MEDIA_DIR), filename)
