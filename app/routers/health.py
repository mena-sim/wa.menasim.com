from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services import runtime_config
from app.services.kb import store

router = APIRouter(tags=["health"])

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _running_commit() -> str:
    """Git commit checked out when THIS process started (frozen at import).

    Computed once at module load so it reflects the running code, not the
    repo on disk after a later `git pull` without a restart.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


# Frozen at process start (import time) — key to detecting "app not restarted".
RUNNING_COMMIT = _running_commit()


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


@router.get("/health/version")
def health_version() -> dict:
    """Commit the running process started with (used by deploy.sh to confirm a restart)."""
    return {"commit": RUNNING_COMMIT}
