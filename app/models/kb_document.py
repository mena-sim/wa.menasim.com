from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KbDocument(Base):
    """Registry of source docs indexed into the vector store (for /kb listing)."""

    __tablename__ = "kb_documents"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(512), index=True)  # file path
    language: Mapped[str] = mapped_column(String(8), default="en")
    title: Mapped[str] = mapped_column(String(255), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
