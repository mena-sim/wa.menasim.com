from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_KEY_FILE = os.path.join("data", "secret.key")


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = (settings.encryption_key or "").strip()
    if not key:
        # Persist a generated key locally so encrypted values survive restarts.
        os.makedirs("data", exist_ok=True)
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE, "rb") as f:
                key = f.read().decode("utf-8").strip()
        else:
            key = Fernet.generate_key().decode("utf-8")
            with open(_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(key)
            logger.info("Generated new local encryption key at %s", _KEY_FILE)
    return Fernet(key.encode("utf-8"))


def encrypt(value: str) -> str:
    if value is None:
        value = ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Failed to decrypt a stored secret (key rotated?)")
        return ""
