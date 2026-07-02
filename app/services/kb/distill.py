from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent import llm
from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)

_LANG_NAME = {"en": "English", "ar": "Arabic"}
_MAX_CHARS_PER_BATCH = 6000

_SYSTEM = (
    "You turn eSIM customer-support chat transcripts into a concise, reusable knowledge base. "
    "Extract only generally useful support knowledge as Q&A entries. "
    "STRICTLY remove all personal data: names, phone numbers, emails, order numbers, ICCIDs, "
    "addresses, and any one-off specifics. Do not invent facts not present in the transcript. "
    "Output GitHub-flavored Markdown only, each entry as:\n"
    "### Q: <question>\n<short answer>\n\n"
    "If the transcript has no reusable knowledge, output nothing."
)


def _batches(text: str) -> list[str]:
    lines = text.splitlines()
    batches: list[str] = []
    buf: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) > _MAX_CHARS_PER_BATCH and buf:
            batches.append("\n".join(buf))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        batches.append("\n".join(buf))
    return batches or [""]


def distill_to_faq(db: Session, transcript: str, language: str = "en") -> str:
    """Distill a transcript into PII-stripped Q&A markdown. Empty string if unavailable."""
    if not runtime_config.llm_enabled(db) or not (transcript or "").strip():
        return ""
    cfg = runtime_config.llm_config(db)
    lang_name = _LANG_NAME.get(language, "English")
    out_parts: list[str] = []
    for batch in _batches(transcript):
        if not batch.strip():
            continue
        try:
            msg = llm.chat(
                [
                    {"role": "system", "content": f"{_SYSTEM}\nWrite the entries in {lang_name}."},
                    {"role": "user", "content": batch},
                ],
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                max_tokens=cfg["max_tokens"],
                temperature=0.2,
            )
            text = (msg.content or "").strip()
            if text:
                out_parts.append(text)
        except Exception:  # pragma: no cover - network/LLM errors shouldn't break import
            logger.exception("distill batch failed")
    return "\n\n".join(out_parts).strip()
