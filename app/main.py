from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.core.logging import get_logger
from app.routers import chat, health, inbox, kb, settings as settings_router, telnyx_webhook
from app.services.kb import ingest

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web" / "static"
MEDIA_DIR = Path("data/media")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        ingest.ensure_indexed(db)
    finally:
        db.close()
    logger.info("%s started", get_settings().app_name)
    yield


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(kb.router)
app.include_router(settings_router.router)
app.include_router(inbox.router)
app.include_router(telnyx_webhook.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


@app.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "chat.html"))


@app.get("/inbox", include_in_schema=False)
def inbox_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "inbox.html"))
