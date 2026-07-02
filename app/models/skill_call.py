from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillCall(Base):
    """Audit log of every agent skill (tool) invocation.

    Fulfils the SKILLS.md global rule: log conversation_id, skill_name, input, output,
    timestamp. Also used to enforce rate guardrails (e.g. resend_qr max 3 / 24h).
    """

    __tablename__ = "skill_calls"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    skill_name: Mapped[str] = mapped_column(String(64), index=True)
    input_json: Mapped[str] = mapped_column(Text, default="")
    output_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
