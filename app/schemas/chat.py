from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(default="", description="Customer message text.")
    session_id: str = Field(default="web-tester", description="Web session identifier.")
    media_url: str | None = Field(default=None, description="Optional inbound image URL.")
    is_image: bool = Field(default=False)


class ChatResponse(BaseModel):
    reply: str
    conversation_id: int
    language: str
    media_url: str | None = None
    escalated: bool = False
    needs_human: bool = False
