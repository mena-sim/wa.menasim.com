#!/usr/bin/env python3
"""Diagnose WhatsApp voice/image handling for a phone number.

Run on the server from the repo root:

    ./scripts/diagnose-whatsapp +447954823445
    .venv/bin/python3 scripts/diagnose_whatsapp.py +447954823445

Shows: voice config, conversation messages (look for 🎤 / 📷), webhook events with media_log.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VENV_PY = ROOT / ".venv" / "bin" / "python3"
if _VENV_PY.is_file() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

sys.path.insert(0, str(ROOT))

from sqlalchemy import desc, select  # noqa: E402

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.webhook_event import WebhookEvent  # noqa: E402
from app.services import runtime_config  # noqa: E402
from app.services.channels.base import InboundMessage  # noqa: E402


def _mask(s: str | None, show: int = 4) -> str:
    if not s:
        return "(not set)"
    if len(s) <= show * 2:
        return "***"
    return f"{s[:show]}...{s[-show:]}"


def _normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


def diagnose(phone: str, test_url: str | None = None) -> None:
    phone = _normalize_phone(phone)
    init_db()
    db = SessionLocal()
    try:
        print("=" * 60)
        print(f"WhatsApp diagnostics for {phone}")
        print("=" * 60)

        print("\n--- Voice / media config ---")
        print(f"  voice_enabled:          {runtime_config.voice_enabled(db)}")
        print(f"  deepinfra_api_key:      {_mask(runtime_config.get(db, 'deepinfra_api_key'))}")
        print(f"  whisper_model:          {runtime_config.get(db, 'deepinfra_whisper_model')}")
        print(f"  vision_model:           {runtime_config.get(db, 'deepinfra_vision_model')}")
        print(f"  telnyx_api_key:         {_mask(runtime_config.get(db, 'telnyx_api_key'))}")
        print(f"  whatsapp_from:          {runtime_config.get(db, 'telnyx_whatsapp_from') or '(not set)'}")

        conv = db.execute(
            select(Conversation).where(
                Conversation.channel == "whatsapp",
                Conversation.sender_id == phone,
            )
        ).scalar_one_or_none()

        print("\n--- Conversation ---")
        if not conv:
            print("  No WhatsApp conversation found for this number.")
        else:
            print(f"  id:           {conv.id}")
            print(f"  language:     {conv.language}")
            print(f"  verified:     {conv.verified} (order: {conv.verified_order or '-'})")
            print(f"  handed_over:  {conv.handed_over}")
            print(f"  needs_human:  {conv.needs_human}")
            print(f"  created_at:   {conv.created_at}")
            print(f"  updated_at:   {conv.updated_at}")

            msgs = (
                db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(desc(Message.created_at))
                    .limit(25)
                )
                .scalars()
                .all()
            )

            print(f"\n--- Last {len(msgs)} messages (newest first) ---")
            for m in msgs:
                content = (m.content or "")[:220]
                flags = []
                if m.media_url:
                    flags.append("HAS_MEDIA_URL")
                if content.startswith("🎤"):
                    flags.append("VOICE_TRANSCRIBED")
                elif content.startswith("🎙️"):
                    flags.append("VOICE_FAIL_MSG")
                if content.startswith("📷") or "📷 [" in content:
                    flags.append("IMAGE_ANALYZED")
                if not content.strip() and m.media_url:
                    flags.append("EMPTY_TEXT+URL")
                if content.strip() == "[image]":
                    flags.append("IMAGE_PLACEHOLDER_ONLY")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                print(f"  [{m.created_at}] {m.role}: {content!r}{flag_str}")

        events = (
            db.execute(
                select(WebhookEvent)
                .where(WebhookEvent.sender == phone)
                .order_by(desc(WebhookEvent.created_at))
                .limit(20)
            )
            .scalars()
            .all()
        )

        print(f"\n--- Last {len(events)} webhook events for this sender ---")
        if not events:
            print("  (none — try sending a test message, then re-run)")
        for e in events:
            print(f"  [{e.created_at}] {e.event_type or '?'} | status={e.status} | event_id={e.event_id or '-'}")
            if not e.detail:
                continue
            try:
                parsed = json.loads(e.detail)
                action = parsed.get("action", parsed.get("parse", ""))
                print(f"    media action: {action}")
                for key in (
                    "transcript_preview",
                    "vision_preview",
                    "error",
                    "download_bytes",
                    "download_content_type",
                    "voice_enabled",
                    "deepinfra_key_set",
                    "input",
                    "parse",
                ):
                    if key in parsed and parsed[key]:
                        val = parsed[key]
                        if isinstance(val, dict):
                            val = json.dumps(val, ensure_ascii=False)
                        print(f"    {key}: {val}")
            except json.JSONDecodeError:
                print(f"    detail: {e.detail[:300]}")

        if test_url:
            print("\n--- Live transcription test ---")
            from app.services import inbound_media

            inbound = InboundMessage(
                channel="whatsapp",
                sender_id=phone,
                is_audio=True,
                media_url=test_url,
                media_content_type="",
            )
            result, media_log = inbound_media.prepare_inbound_media(db, inbound)
            if isinstance(result, str):
                print(f"  user_message: {result!r}")
            else:
                print(f"  user_text: {result.text!r}")
            print(f"  media_log: {json.dumps(media_log, ensure_ascii=False, indent=2)}")

        print("\n--- How to read this ---")
        print("  VOICE_TRANSCRIBED  = voice converted to text (🎤 in chat history)")
        print("  VOICE_FAIL_MSG     = our app replied that transcription failed")
        print("  IMAGE_PLACEHOLDER  = voice may have been misclassified as image → LLM says can't read audio")
        print("  media_log.action:")
        print("    transcribed                 = OK")
        print("    transcribe_error            = download or Whisper failed (see error)")
        print("    transcribe_skipped_*        = voice disabled or missing DeepInfra key")
        print("    media_not_handled           = attachment not classified as audio/image")
        print("    vision_ok / vision_error    = screenshot path")
        print("=" * 60)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose WhatsApp voice/media for a phone number")
    parser.add_argument("phone", help="E.g. +447954823445")
    parser.add_argument(
        "--test-url",
        help="Optional Telnyx media URL to test download + Whisper live",
    )
    args = parser.parse_args()
    diagnose(args.phone, test_url=args.test_url)


if __name__ == "__main__":
    main()
