from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import inbound_media, runtime_config, transcription
from app.services.channels.web_channel import handle_web_message

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

MEDIA_DIR = Path("data/media")
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    result = handle_web_message(
        db,
        session_id=req.session_id,
        text=req.message,
        media_url=req.media_url,
        is_image=req.is_image,
    )
    return ChatResponse(
        reply=result.reply,
        conversation_id=result.conversation_id,
        language=result.language,
        media_url=result.media_url,
        escalated=result.escalated,
        needs_human=result.needs_human,
    )


@router.post("/chat/voice")
async def chat_voice(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Accept a recorded voice note, transcribe it (DeepInfra Whisper), then answer."""
    if not runtime_config.voice_enabled(db):
        raise HTTPException(status_code=400, detail="Voice transcription is not configured.")
    audio = await file.read()
    try:
        tr = transcription.transcribe(
            db,
            audio,
            filename=file.filename or "voice.webm",
            content_type=file.content_type or "audio/webm",
        )
    except transcription.TranscriptionError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")
    text = tr.get("text", "").strip()
    if not text:
        return {
            "transcript": "",
            "reply": "Sorry, I couldn't hear anything in that voice note. Please try again or type your message.",
            "conversation_id": None,
            "language": tr.get("language"),
            "media_url": None,
            "escalated": False,
            "needs_human": False,
        }
    result = handle_web_message(db, session_id=session_id, text=text)
    return {
        "transcript": text,
        "reply": result.reply,
        "conversation_id": result.conversation_id,
        "language": result.language,
        "media_url": result.media_url,
        "escalated": result.escalated,
        "needs_human": result.needs_human,
    }


@router.post("/chat/image")
async def chat_image(
    session_id: str = Form(...),
    caption: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Accept an uploaded screenshot/image, store it, then let the agent respond."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are supported.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_IMAGE_EXT:
        ext = ".png"
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = f"up_{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    media_url = f"/media/{name}"
    from app.services.channels.base import InboundMessage

    inbound = InboundMessage(
        channel="web",
        sender_id=session_id or "web-tester",
        text=(caption or "").strip(),
        media_url=media_url,
        is_image=True,
        media_content_type=file.content_type or "image/jpeg",
    )
    prepared, _media_log = inbound_media.prepare_inbound_media(db, inbound)
    if isinstance(prepared, str):
        return {
            "uploaded_url": media_url,
            "reply": prepared,
            "conversation_id": None,
            "language": "ar" if any("\u0600" <= c <= "\u06ff" for c in (caption or "")) else "en",
            "media_url": None,
            "escalated": False,
            "needs_human": False,
        }
    inbound = prepared
    result = handle_web_message(
        db,
        session_id=session_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
    )
    return {
        "uploaded_url": media_url,
        "reply": result.reply,
        "conversation_id": result.conversation_id,
        "language": result.language,
        "media_url": result.media_url,
        "escalated": result.escalated,
        "needs_human": result.needs_human,
    }
