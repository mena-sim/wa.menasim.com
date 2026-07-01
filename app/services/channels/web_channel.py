from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.engine import AgentResult, handle_message

WEB_CHANNEL = "web"


def handle_web_message(
    db: Session,
    *,
    session_id: str,
    text: str,
    media_url: str | None = None,
    is_image: bool = False,
) -> AgentResult:
    return handle_message(
        db,
        channel=WEB_CHANNEL,
        sender_id=session_id or "web-tester",
        text=text,
        media_url=media_url,
        is_image=is_image,
    )
