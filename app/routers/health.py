from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services import runtime_config
from app.services.kb import store

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    return {
        "status": "ok",
        "app": get_settings().app_name,
        "llm_enabled": runtime_config.llm_enabled(db),
        "woocommerce_enabled": runtime_config.woocommerce_enabled(db),
        "whatsapp_enabled": runtime_config.whatsapp_enabled(db),
        "smtp_enabled": runtime_config.smtp_enabled(db),
    }


@router.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/health/kb")
def health_kb() -> dict:
    return {"status": "ok", "chunks": store.count()}
