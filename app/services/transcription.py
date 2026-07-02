from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)

# DeepInfra exposes an OpenAI-compatible audio transcription endpoint.
# Whisper auto-detects the spoken language (Arabic / English / etc.).
_DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/audio/transcriptions"


class TranscriptionError(RuntimeError):
    pass


def transcribe(
    db: Session,
    audio: bytes,
    *,
    filename: str = "voice.webm",
    content_type: str = "audio/webm",
) -> dict:
    """Transcribe an audio blob via DeepInfra Whisper. Returns {text, language}."""
    cfg = runtime_config.transcription_config(db)
    if not cfg["api_key"]:
        raise TranscriptionError("DeepInfra API key is not configured.")
    if not audio:
        raise TranscriptionError("Empty audio.")
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                _DEEPINFRA_URL,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                data={"model": cfg["model"]},
                files={"file": (filename, audio, content_type)},
            )
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Connection error: {exc}") from exc
    if resp.status_code >= 300:
        raise TranscriptionError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    return {
        "text": (payload.get("text") or "").strip(),
        "language": payload.get("language"),
    }
