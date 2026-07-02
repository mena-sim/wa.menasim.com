from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.services.kb import ingest

LANGUAGES = ("en", "ar")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class KbDocError(ValueError):
    pass


def slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", (title or "").strip().lower()).strip("-")
    return slug or "doc"


def _safe_path(source: str) -> Path:
    """Resolve a 'lang/file.md' source under KB_ROOT, rejecting traversal."""
    source = (source or "").replace("\\", "/").strip().lstrip("/")
    parts = [p for p in source.split("/") if p not in ("", ".", "..")]
    if len(parts) != 2 or parts[0] not in LANGUAGES or not parts[1].endswith(".md"):
        raise KbDocError("Invalid document source. Expected '<en|ar>/<name>.md'.")
    path = (ingest.KB_ROOT / parts[0] / parts[1]).resolve()
    if not str(path).startswith(str(ingest.KB_ROOT.resolve())):
        raise KbDocError("Path outside knowledge base.")
    return path


def read_doc(source: str) -> str:
    path = _safe_path(source)
    if not path.exists():
        raise KbDocError("Document not found.")
    return path.read_text(encoding="utf-8")


def _unique_path(language: str, slug: str) -> Path:
    base = ingest.KB_ROOT / language
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / f"{slug}.md"
    i = 2
    while candidate.exists():
        candidate = base / f"{slug}-{i}.md"
        i += 1
    return candidate


def create_doc(db: Session, *, title: str, language: str, body: str) -> dict:
    if language not in LANGUAGES:
        raise KbDocError("Language must be 'en' or 'ar'.")
    if not (title or "").strip():
        raise KbDocError("Title is required.")
    path = _unique_path(language, slugify(title))
    heading = f"# {title.strip()}\n\n"
    content = body if body.lstrip().startswith("#") else heading + (body or "")
    path.write_text(content, encoding="utf-8")
    result = ingest.reindex(db)
    return {"source": f"{language}/{path.name}", **result}


def save_doc(db: Session, *, source: str, body: str) -> dict:
    path = _safe_path(source)
    if not path.exists():
        raise KbDocError("Document not found.")
    path.write_text(body or "", encoding="utf-8")
    result = ingest.reindex(db)
    return {"source": source, **result}


def delete_doc(db: Session, *, source: str) -> dict:
    path = _safe_path(source)
    if path.exists():
        path.unlink()
    result = ingest.reindex(db)
    return {"deleted": source, **result}


def append_entry(db: Session, *, language: str, filename: str, markdown: str) -> dict:
    """Append a block to (or create) a KB file, then reindex. Used by capture/import."""
    if language not in LANGUAGES:
        language = "en"
    base = ingest.KB_ROOT / language
    base.mkdir(parents=True, exist_ok=True)
    path = base / filename
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "\n\n" if existing.strip() else ""
    path.write_text(existing + sep + markdown.strip() + "\n", encoding="utf-8")
    result = ingest.reindex(db)
    return {"source": f"{language}/{path.name}", **result}
