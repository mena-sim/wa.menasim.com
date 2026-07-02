from __future__ import annotations

import base64
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config
from app.services.telnyx_media import fetch_media

logger = get_logger(__name__)

_VISION_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
_DEFAULT_MODEL = "meta-llama/Llama-3.2-11B-Vision-Instruct"

_ANALYSIS_PROMPT = (
    "You help menasim eSIM phone support. Describe this screenshot for a support agent. "
    "Focus on: phone settings, eSIM/cellular line ON or OFF, data roaming, mobile data line, "
    "ICCID if visible, error messages, no signal, airplane mode. "
    "Reply in 2-5 short sentences in the same language as the UI text (Arabic or English)."
)


class VisionError(RuntimeError):
    pass


def vision_enabled(db: Session) -> bool:
    return bool((runtime_config.get(db, "deepinfra_api_key") or "").strip())


def _vision_config(db: Session) -> dict[str, str]:
    return {
        "api_key": runtime_config.get(db, "deepinfra_api_key") or "",
        "model": runtime_config.get(db, "deepinfra_vision_model") or _DEFAULT_MODEL,
    }


def _to_data_url(data: bytes, content_type: str) -> str:
    ct = content_type if content_type.startswith("image/") else "image/jpeg"
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{ct};base64,{b64}"


def analyze_image_bytes(db: Session, data: bytes, *, content_type: str = "image/jpeg") -> str:
    cfg = _vision_config(db)
    if not cfg["api_key"]:
        raise VisionError("DeepInfra API key is not configured.")
    if not data:
        raise VisionError("Empty image.")

    payload = {
        "model": cfg["model"],
        "max_tokens": 512,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _ANALYSIS_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(data, content_type)},
                    },
                ],
            }
        ],
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                _VISION_URL,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise VisionError(f"Connection error: {exc}") from exc
    if resp.status_code >= 300:
        raise VisionError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    text = (
        ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ).strip()
    if not text:
        raise VisionError("Vision model returned empty text.")
    return text


def analyze_image_url(db: Session, url: str, *, content_type: str = "") -> str:
    """Analyze a screenshot from a URL or local /media/ path."""
    if url.startswith("/media/"):
        name = url.rsplit("/", 1)[-1]
        path = Path("data/media") / name
        if not path.is_file():
            raise VisionError(f"Image file not found: {url}")
        data = path.read_bytes()
        ct = content_type or "image/jpeg"
        return analyze_image_bytes(db, data, content_type=ct)

    data, ct = fetch_media(db, url)
    return analyze_image_bytes(db, data, content_type=content_type or ct or "image/jpeg")
