from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.core.security import signing_secret

# Stateless, signed bearer tokens. They survive app restarts/deploys (unlike the
# old in-memory scheme) and stay valid for a long time so the admin stays logged
# in. A token is invalidated automatically if the admin password changes.
_TOKEN_TTL = 60 * 60 * 24 * 365  # 1 year


def verify_password(password: str) -> bool:
    expected = get_settings().admin_password or ""
    return hmac.compare_digest(str(password or ""), str(expected))


def _key() -> bytes:
    # Bind tokens to the admin password so changing it logs everyone out.
    pw = (get_settings().admin_password or "").encode("utf-8")
    return hashlib.sha256(signing_secret() + b"|admin|" + pw).digest()


def _sign(msg: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_key(), msg, hashlib.sha256).digest()).decode().rstrip("=")


def issue_token() -> str:
    issued = str(int(time.time()))
    payload = base64.urlsafe_b64encode(issued.encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload.encode())}"


def _valid(token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(payload.encode())):
        return False
    try:
        pad = "=" * (-len(payload) % 4)
        issued = int(base64.urlsafe_b64decode(payload + pad).decode())
    except (ValueError, TypeError):
        return False
    return (time.time() - issued) <= _TOKEN_TTL


def revoke(token: str) -> None:
    # Stateless tokens can't be individually revoked; logout is handled client-side.
    # (Changing ADMIN_PASSWORD invalidates all existing tokens.)
    return None


def require_admin(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: require a valid admin bearer token."""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not _valid(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
