from __future__ import annotations

import hashlib
import re

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

_EMBED_MODEL = "all-MiniLM-L6-v2"
_DIM = 384

_model = None
_mode: str | None = None  # "st" (sentence-transformers) | "fallback"
_token_re = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)


def _try_load_st():
    """Attempt to load the sentence-transformers model; return it or None."""
    global _model
    try:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading local embedding model %s ...", _EMBED_MODEL)
        _model = SentenceTransformer(_EMBED_MODEL)
        return _model
    except Exception as exc:  # offline / SSL / missing model
        logger.warning(
            "Could not load '%s' (%s). Falling back to offline hashing embeddings. "
            "For best quality, allow access to huggingface.co or pre-download the model.",
            _EMBED_MODEL,
            type(exc).__name__,
        )
        return None


def _ensure_mode() -> str:
    global _mode
    if _mode is None:
        _mode = "st" if _try_load_st() is not None else "fallback"
    return _mode


def _hash_embed(texts: list[str]) -> np.ndarray:
    """Deterministic offline embedding: feature-hashed word + char-trigram bag.

    Lower semantic quality than a transformer, but works fully offline and gives
    reasonable lexical matching for a support KB.
    """
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, text in enumerate(texts):
        tokens = _token_re.findall((text or "").lower())
        features: list[str] = list(tokens)
        for tok in tokens:
            padded = f"#{tok}#"
            features.extend(padded[j : j + 3] for j in range(len(padded) - 2))
        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest(), 16)
            idx = h % _DIM
            sign = 1.0 if (h >> 8) & 1 else -1.0
            out[i, idx] += sign
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, _DIM), dtype=np.float32)
    if _ensure_mode() == "st":
        vecs = _model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)
    return _hash_embed(texts)


def embed_one(text: str) -> np.ndarray:
    return embed([text])[0]


def embedding_mode() -> str:
    return _ensure_mode()
