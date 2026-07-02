from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import runtime_config, transcription
from app.services.channels.web_channel import handle_web_message

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


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
