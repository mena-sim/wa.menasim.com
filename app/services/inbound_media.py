from __future__ import annotations

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


def prepare_inbound_media(db: Session, inbound: InboundMessage) -> InboundMessage | str:
    """Transcribe voice, analyze images, or return a user-facing error string."""
    if inbound.is_unsupported_media:
        return _IMAGES_ONLY_AR if _lang(inbound) == "ar" else _IMAGES_ONLY_EN

    if inbound.is_audio and inbound.media_url:
        if not runtime_config.voice_enabled(db):
            return _VOICE_FAIL_AR if _lang(inbound) == "ar" else _VOICE_FAIL_EN
        try:
            audio, ct = fetch_media(db, inbound.media_url)
            ext = "ogg" if "ogg" in ct else ("mp3" if "mpeg" in ct or "mp3" in ct else "m4a")
            tr = transcription.transcribe(
                db, audio, filename=f"voice.{ext}", content_type=ct or "audio/ogg"
            )
            transcript = (tr.get("text") or "").strip()
            if transcript:
                prefix = inbound.text.strip() if inbound.text else ""
                inbound.text = f"{prefix}\n{transcript}".strip() if prefix else transcript
                inbound.is_audio = False
                inbound.is_image = False
                inbound.media_url = None
                return inbound
        except Exception:
            logger.exception("voice transcription failed url=%s", inbound.media_url)
        return _VOICE_FAIL_AR if _lang(inbound) == "ar" else _VOICE_FAIL_EN

    if inbound.is_image and inbound.media_url:
        if not vision.vision_enabled(db):
            inbound.text = (
                f"{inbound.text}\n[image attached — vision not configured]"
                if inbound.text
                else "[image attached — vision not configured]"
            ).strip()
            inbound.is_image = False
            return inbound
        try:
            description = vision.analyze_image_url(
                db, inbound.media_url, content_type=inbound.media_content_type or ""
            )
            label = "لقطة الشاشة" if _lang(inbound) == "ar" else "Screenshot"
            block = f"[{label}: {description}]"
            inbound.text = f"{inbound.text}\n{block}".strip() if inbound.text else block
            inbound.is_image = False
            inbound.media_url = None
            return inbound
        except Exception:
            logger.exception("screenshot vision failed url=%s", inbound.media_url)
            return _VISION_FAIL_AR if _lang(inbound) == "ar" else _VISION_FAIL_EN

    return inbound
