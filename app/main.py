from __future__ import annotations

# Trust the OS certificate store (fixes SSL "unable to get local issuer certificate"
# behind corporate proxies / antivirus TLS interception). Must run before any HTTPS
# connection is made (DeepSeek API, huggingface model download). Safe no-op if missing.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.core.logging import get_logger
from app.routers import (
    admin,
    chat,
    health,
    inbox,
    kb,
    settings as settings_router,
    telnyx_webhook,
)
from app.services.kb import ingest

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "web" / "static"
ADMIN_DIST = REPO_ROOT / "admin-web" / "dist"
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


@app.middleware("http")
async def _revalidate_web_assets(request, call_next):
    """Force browsers/proxies to revalidate the chat UI + its (non-hashed) static
    assets so a deploy is picked up immediately instead of serving a stale cache.
    ETag/Last-Modified still allow efficient 304s when nothing changed."""
    response = await call_next(request)
    path = request.url.path
    if (
        path in ("/", "/inbox", "/admin")
        or path.startswith("/static")
        or (path.startswith("/admin") and not path.startswith("/admin/assets"))
    ):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(kb.router)
app.include_router(settings_router.router)
app.include_router(inbox.router)
app.include_router(admin.router)
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


# Serve the built React admin console at /admin (after `npm run build` in admin-web/).
if ADMIN_DIST.exists():
    app.mount("/admin/assets", StaticFiles(directory=str(ADMIN_DIST / "assets")), name="admin-assets")

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/{full_path:path}", include_in_schema=False)
    def admin_spa(full_path: str = "") -> FileResponse:
        return FileResponse(str(ADMIN_DIST / "index.html"))
else:

    @app.get("/admin", include_in_schema=False)
    def admin_not_built() -> dict:
        return {
            "message": "Admin console not built yet. In admin-web/ run: npm install && npm run build. "
            "For development run the Vite dev server (npm run dev) on port 5174."
        }
