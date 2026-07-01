from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.kb_document import KbDocument
from app.services.kb import ingest, store

router = APIRouter(prefix="/api/kb", tags=["kb"])


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)) -> dict:
    docs = db.execute(select(KbDocument).order_by(KbDocument.source)).scalars().all()
    return {
        "chunks": store.count(),
        "documents": [
            {
                "source": d.source,
                "language": d.language,
                "title": d.title,
                "chunk_count": d.chunk_count,
                "indexed_at": d.indexed_at.isoformat() if d.indexed_at else None,
            }
            for d in docs
        ],
    }


@router.post("/reindex")
def reindex(db: Session = Depends(get_db)) -> dict:
    return {"reindexed": True, **ingest.reindex(db)}


@router.get("/search")
def search(q: str, language: str | None = None, top_k: int = 4) -> dict:
    return {"results": store.query(q, language=language, top_k=top_k)}
