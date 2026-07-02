from __future__ import annotations

import hmac
import secrets
import time

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

# In-memory bearer tokens: token -> expiry epoch. Fine for a single-node prototype;
# tokens are lost on restart (admin simply logs in again).
_TOKENS: dict[str, float] = {}
_TOKEN_TTL = 60 * 60 * 12  # 12 hours


def verify_password(password: str) -> bool:
    expected = get_settings().admin_password or ""
    return hmac.compare_digest(str(password or ""), str(expected))


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time() + _TOKEN_TTL
    return token


def _valid(token: str) -> bool:
    exp = _TOKENS.get(token)
    if not exp:
        return False
    if time.time() > exp:
        _TOKENS.pop(token, None)
        return False
    return True


def revoke(token: str) -> None:
    _TOKENS.pop(token, None)


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
