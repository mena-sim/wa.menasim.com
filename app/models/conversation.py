from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)  # web | whatsapp | sms
    sender_id: Mapped[str] = mapped_column(String(128), index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    handed_over: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    # Identity verification: set once the customer confirms order number + matching email.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_order: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket", back_populates="conversation", cascade="all, delete-orphan"
    )
