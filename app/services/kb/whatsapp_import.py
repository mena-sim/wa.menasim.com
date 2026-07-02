from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.kb import distill, docs

logger = get_logger(__name__)

# iOS:      [12/07/2026, 21:30:45] Name: message
# Android:  12/07/2026, 21:30 - Name: message   (or "21:30 pm")
_IOS_RE = re.compile(r"^\[(?P<meta>[^\]]+)\]\s(?P<name>[^:]+):\s(?P<msg>.*)$")
_ANDROID_RE = re.compile(
    r"^(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}),\s(?P<time>[\d:apmAPM\.\s]+?)\s-\s(?P<name>[^:]+):\s(?P<msg>.*)$"
)
# Timestamped lines with no "Name:" are WhatsApp system notices (encryption, etc.).
_IOS_SYS_RE = re.compile(r"^\[[^\]]+\]\s[^:]+$")
_ANDROID_SYS_RE = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4},\s[\d:apmAPM\.\s]+?\s-\s[^:]+$")

_SYSTEM_MARKERS = (
    "Messages and calls are end-to-end encrypted",
    "<Media omitted>",
    "media omitted",
    "This message was deleted",
    "changed the subject",
    "changed this group's icon",
    "created group",
    "added you",
    "joined using this group's invite link",
    "null",
)


@dataclass
class Turn:
    name: str
    text: str


def _is_system(name: str, text: str) -> bool:
    blob = f"{name}: {text}"
    return any(m in blob for m in _SYSTEM_MARKERS)


def parse_export(raw: str) -> list[Turn]:
    """Parse a WhatsApp chat export into ordered turns, joining multi-line messages."""
    turns: list[Turn] = []
    for line in raw.replace("\u200e", "").splitlines():
        m = _IOS_RE.match(line) or _ANDROID_RE.match(line)
        if m:
            name = m.group("name").strip()
            text = m.group("msg").strip()
            if _is_system(name, text):
                continue
            turns.append(Turn(name=name, text=text))
        elif _IOS_SYS_RE.match(line) or _ANDROID_SYS_RE.match(line):
            # timestamped system notice with no sender -> skip
            continue
        elif turns:
            # continuation of the previous message
            turns[-1].text += "\n" + line.rstrip()
    return turns


def _arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    ar = sum(1 for c in letters if "\u0600" <= c <= "\u06ff")
    return ar / len(letters)


def build_transcript(turns: list[Turn], support_name: str) -> str:
    support = (support_name or "").strip().lower()
    lines: list[str] = []
    for t in turns:
        if not t.text.strip():
            continue
        role = "Agent" if support and support in t.name.lower() else "Customer"
        lines.append(f"{role}: {t.text}")
    return "\n".join(lines)


def import_export(
    db: Session,
    *,
    raw: str,
    support_name: str,
    language: str = "auto",
    distill_with_ai: bool = True,
) -> dict:
    turns = parse_export(raw)
    transcript = build_transcript(turns, support_name)
    if not transcript.strip():
        return {"messages": len(turns), "written": False, "reason": "no_usable_messages"}

    lang = language if language in ("en", "ar") else ("ar" if _arabic_ratio(transcript) > 0.3 else "en")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if distill_with_ai:
        body = distill.distill_to_faq(db, transcript, lang)
        if not body:
            # LLM unavailable -> fall back to raw transcript so nothing is lost
            body = f"# Imported WhatsApp support history\n\n{transcript}"
        else:
            body = f"# WhatsApp support knowledge (imported)\n\n{body}"
    else:
        body = f"# Imported WhatsApp support history\n\n{transcript}"

    result = docs.append_entry(
        db, language=lang, filename=f"whatsapp-{stamp}.md", markdown=body
    )
    return {
        "messages": len(turns),
        "language": lang,
        "distilled": bool(distill_with_ai),
        "written": True,
        **result,
    }
