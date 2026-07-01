from __future__ import annotations

import json
import os
import threading
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.kb.embeddings import embed, embed_one

logger = get_logger(__name__)

_lock = threading.Lock()
_loaded = False
_ids: list[str] = []
_docs: list[str] = []
_metas: list[dict[str, Any]] = []
_vecs: np.ndarray | None = None  # shape [n, dim], L2-normalized


def _dir() -> str:
    return get_settings().chroma_dir


def _vec_path() -> str:
    return os.path.join(_dir(), "kb_vecs.npy")


def _meta_path() -> str:
    return os.path.join(_dir(), "kb_meta.json")


def _load() -> None:
    global _loaded, _ids, _docs, _metas, _vecs
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        if os.path.exists(_vec_path()) and os.path.exists(_meta_path()):
            try:
                _vecs = np.load(_vec_path())
                with open(_meta_path(), "r", encoding="utf-8") as f:
                    payload = json.load(f)
                _ids = payload.get("ids", [])
                _docs = payload.get("docs", [])
                _metas = payload.get("metas", [])
            except Exception as exc:
                logger.warning("kb store load failed, starting empty: %s", exc)
                _ids, _docs, _metas, _vecs = [], [], [], None
        _loaded = True


def _persist() -> None:
    os.makedirs(_dir(), exist_ok=True)
    if _vecs is not None and len(_ids):
        np.save(_vec_path(), _vecs)
    with open(_meta_path(), "w", encoding="utf-8") as f:
        json.dump({"ids": _ids, "docs": _docs, "metas": _metas}, f, ensure_ascii=False)


def reset_collection() -> None:
    global _loaded, _ids, _docs, _metas, _vecs
    with _lock:
        _ids, _docs, _metas, _vecs = [], [], [], None
        _loaded = True
        os.makedirs(_dir(), exist_ok=True)
        for p in (_vec_path(), _meta_path()):
            if os.path.exists(p):
                os.remove(p)


def add_chunks(ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
    global _vecs
    if not ids:
        return
    _load()
    new_vecs = embed(documents)
    with _lock:
        _ids.extend(ids)
        _docs.extend(documents)
        _metas.extend(metadatas)
        _vecs = new_vecs if _vecs is None else np.vstack([_vecs, new_vecs])
        _persist()


def query(text: str, *, language: str | None = None, top_k: int = 4) -> list[dict[str, Any]]:
    _load()
    if _vecs is None or not len(_ids):
        return []
    q = embed_one(text)  # normalized
    sims = _vecs @ q  # cosine similarity (both normalized)
    order = np.argsort(-sims)
    out: list[dict[str, Any]] = []
    for idx in order:
        meta = _metas[idx]
        if language and meta.get("language") != language:
            continue
        out.append(
            {"text": _docs[idx], "metadata": meta, "distance": float(1.0 - sims[idx])}
        )
        if len(out) >= top_k:
            break
    return out


def count() -> int:
    _load()
    return len(_ids)
