from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config, transcription, vision
from app.services.channels.base import InboundMessage
from app.services.telnyx_media import fetch_media

logger = get_logger(__name__)

_IMAGES_ONLY_AR = (
    "أرسل لقطة شاشة (صورة) فقط — ما أقدر أفتح ملفات PDF أو مستندات أو فيديو.\n"
    "لو عندك مشكلة، صوّر شاشة الإعدادات وأرسلها كصورة."
)
_IMAGES_ONLY_EN = (
    "Please send a screenshot as an image only — I can't open PDFs, documents, or video files.\n"
    "If you need help, take a photo of your settings screen and send it as an image."
)

_VOICE_FAIL_AR = (
    "🎙️ عذرًا، ما قدرت أحوّل الرسالة الصوتية لنص. تأكد أن DeepInfra مفعّل في الإعدادات، "
    "أو اكتب رسالتك نصيًا."
)
_VOICE_FAIL_EN = (
    "🎙️ Sorry, I couldn't transcribe that voice note. Please type your message as text."
)

_VISION_FAIL_AR = "ما قدرت أقرأ الصورة. جرّب ترسلها مرة ثانية أو اكتب وصف المشكلة."
_VISION_FAIL_EN = "I couldn't read that image. Try sending it again or describe the issue in text."


def _lang(inbound: InboundMessage) -> str:
    for ch in inbound.text or "":
        if "\u0600" <= ch <= "\u06ff":
            return "ar"
    return "en"


def media_input_log(inbound: InboundMessage) -> dict[str, Any]:
    return {
        "is_audio": inbound.is_audio,
        "is_image": inbound.is_image,
        "is_unsupported": inbound.is_unsupported_media,
        "content_type": inbound.media_content_type or "",
        "has_media_url": bool(inbound.media_url),
        "text_len": len(inbound.text or ""),
    }


def prepare_inbound_media(
    db: Session, inbound: InboundMessage
) -> tuple[InboundMessage | str, dict[str, Any]]:
    """Transcribe voice, analyze images, or return a user-facing error string + log dict."""
    log: dict[str, Any] = {"input": media_input_log(inbound), "action": "none"}

    if inbound.is_unsupported_media:
        log["action"] = "rejected_unsupported"
        msg = _IMAGES_ONLY_AR if _lang(inbound) == "ar" else _IMAGES_ONLY_EN
        return msg, log

    # Voice / audio (including ambiguous WhatsApp voice notes with no MIME type).
    if inbound.is_audio and inbound.media_url:
        log["action"] = "transcribe_attempt"
        if not runtime_config.voice_enabled(db):
            log["action"] = "transcribe_skipped_voice_disabled"
            log["voice_enabled"] = False
            log["deepinfra_key_set"] = bool(runtime_config.get(db, "deepinfra_api_key"))
            return (_VOICE_FAIL_AR if _lang(inbound) == "ar" else _VOICE_FAIL_EN), log
        try:
            audio, ct = fetch_media(db, inbound.media_url)
            log["download_bytes"] = len(audio)
            log["download_content_type"] = ct
            ext = "ogg" if "ogg" in ct else ("mp3" if "mpeg" in ct or "mp3" in ct else "m4a")
            tr = transcription.transcribe(
                db, audio, filename=f"voice.{ext}", content_type=ct or "audio/ogg"
            )
            transcript = (tr.get("text") or "").strip()
            log["whisper_language"] = tr.get("language")
            if transcript:
                prefix = inbound.text.strip() if inbound.text else ""
                body = f"{prefix}\n{transcript}".strip() if prefix else transcript
                inbound.text = f"🎤 {body}"
                inbound.is_audio = False
                inbound.is_image = False
                inbound.media_url = None
                log["action"] = "transcribed"
                log["transcript_preview"] = transcript[:120]
                return inbound, log
            log["action"] = "transcribe_empty"
        except Exception as exc:
            log["action"] = "transcribe_error"
            log["error"] = str(exc)[:300]
            logger.exception("voice transcription failed url=%s", inbound.media_url)
        return (_VOICE_FAIL_AR if _lang(inbound) == "ar" else _VOICE_FAIL_EN), log

    if inbound.is_image and inbound.media_url:
        log["action"] = "vision_attempt"
        if not vision.vision_enabled(db):
            inbound.text = (
                f"{inbound.text}\n[image attached — vision not configured]"
                if inbound.text
                else "[image attached — vision not configured]"
            ).strip()
            inbound.is_image = False
            log["action"] = "vision_skipped_not_configured"
            return inbound, log
        try:
            description = vision.analyze_image_url(
                db, inbound.media_url, content_type=inbound.media_content_type or ""
            )
            label = "لقطة الشاشة" if _lang(inbound) == "ar" else "Screenshot"
            block = f"📷 [{label}: {description}]"
            inbound.text = f"{inbound.text}\n{block}".strip() if inbound.text else block
            inbound.is_image = False
            inbound.media_url = None
            log["action"] = "vision_ok"
            log["vision_preview"] = description[:120]
            return inbound, log
        except Exception as exc:
            log["action"] = "vision_error"
            log["error"] = str(exc)[:300]
            logger.exception("screenshot vision failed url=%s", inbound.media_url)
            return (_VISION_FAIL_AR if _lang(inbound) == "ar" else _VISION_FAIL_EN), log

    if inbound.media_url:
        log["action"] = "media_not_handled"
    return inbound, log
