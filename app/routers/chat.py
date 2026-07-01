from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.channels.web_channel import handle_web_message

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
