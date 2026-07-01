from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services.kb import store

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "app": s.app_name,
        "llm_enabled": s.llm_enabled,
        "woocommerce_enabled": s.woocommerce_enabled,
        "whatsapp_enabled": s.whatsapp_enabled,
        "smtp_enabled": s.smtp_enabled,
    }


@router.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/health/kb")
def health_kb() -> dict:
    return {"status": "ok", "chunks": store.count()}
