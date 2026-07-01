from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.kb_document import KbDocument
from app.services.kb import store

logger = get_logger(__name__)

KB_ROOT = Path(__file__).resolve().parents[3] / "kb_docs"
_LANGUAGES = ("en", "ar")


def _chunk_markdown(text: str, *, max_chars: int = 900) -> list[str]:
    """Split on markdown headings, then pack into <= max_chars chunks."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    chunks: list[str] = []
    buf = ""
    for block in blocks:
        if not block:
            continue
        if len(buf) + len(block) + 2 <= max_chars:
            buf = f"{buf}\n\n{block}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(block) <= max_chars:
                buf = block
            else:
                for i in range(0, len(block), max_chars):
                    chunks.append(block[i : i + max_chars])
                buf = ""
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def reindex(db: Session) -> dict:
    """Full rebuild of the vector store from kb_docs/{en,ar}/*.md."""
    store.reset_collection()
    db.execute(delete(KbDocument))
    db.commit()

    total_docs = 0
    total_chunks = 0
    for lang in _LANGUAGES:
        lang_dir = KB_ROOT / lang
        if not lang_dir.exists():
            continue
        for md_path in sorted(lang_dir.glob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            title = md_path.stem.replace("-", " ").title()
            chunks = _chunk_markdown(text)
            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict] = []
            rel = f"{lang}/{md_path.name}"
            for idx, chunk in enumerate(chunks):
                digest = hashlib.md5(f"{rel}:{idx}".encode()).hexdigest()
                ids.append(digest)
                docs.append(chunk)
                metas.append({"source": rel, "language": lang, "title": title})
            store.add_chunks(ids, docs, metas)

            db.add(
                KbDocument(
                    source=rel,
                    language=lang,
                    title=title,
                    chunk_count=len(chunks),
                )
            )
            total_docs += 1
            total_chunks += len(chunks)

    db.commit()
    logger.info("KB reindex complete: %d docs, %d chunks", total_docs, total_chunks)
    return {"documents": total_docs, "chunks": total_chunks}


def ensure_indexed(db: Session) -> None:
    """Index once on startup if the store is empty."""
    if store.count() == 0:
        try:
            reindex(db)
        except Exception as exc:
            logger.warning("KB initial index skipped: %s", exc)
