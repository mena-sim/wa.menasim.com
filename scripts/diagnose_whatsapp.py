#!/usr/bin/env python3
"""Diagnose WhatsApp voice/image handling for a phone number.

Run on the server from the repo root:

    ./scripts/diagnose-whatsapp +447954823445
    ./scripts/diagnose-whatsapp +447954823445 --test-last-audio
    ./scripts/diagnose-whatsapp +447954823445 --test-url "https://..."

Shows: voice config, conversation messages (look for 🎤 / 📷), webhook events with media_log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
from app.services.channels.whatsapp_telnyx_channel import (  # noqa: E402
    _extract_from_text_blob,
    _URL_IN_TEXT_RE,
)


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


def _find_audio_url_in_text(text: str) -> str | None:
    if not text:
        return None
    url, _, _, _ = _extract_from_text_blob(text)
    if url:
        return url
    m = _URL_IN_TEXT_RE.search(text)
    return m.group(1) if m else None


def _verdict_from_media_log(log: dict) -> str:
    action = log.get("action") or "none"
    parse = log.get("parse") or log.get("input") or {}
    hint = log.get("hint") or ""
    error = log.get("error") or ""
    payload_debug = parse.get("payload_debug") or log.get("payload_debug") or {}

    if action == "transcribed":
        return "OK — voice transcribed successfully"
    if action == "transcribe_error":
        return f"FAIL — download or Whisper error: {error}"
    if action.startswith("transcribe_skipped"):
        return f"FAIL — transcription not enabled/configured ({action})"
    if hint == "empty_inbound_no_text_or_media":
        return (
            "FAIL — Telnyx webhook had NO text and NO media URL (parser found nothing to transcribe). "
            "Check payload_debug below — voice may not be in the webhook at all."
        )
    if hint == "audio_dict_in_text_not_recognized" or payload_debug.get("parse_error"):
        return (
            "FAIL — voice dict seen in text but audio URL could not be extracted "
            "(often truncated/malformed payload.text)"
        )
    if parse.get("parsed_audio") and not action.startswith("transcribe"):
        return "FAIL — parsed as audio but transcription did not run (unexpected)"
    if not parse.get("parsed_audio") and parse.get("text_len", 0) > 0 and "audio" in str(
        payload_debug.get("text_preview") or ""
    ):
        return "FAIL — audio dict in payload.text but NOT parsed as audio (deploy latest code or broken dict)"
    if action == "none" and parse.get("text_len", 0) > 100:
        return "FAIL — message treated as plain text, not voice (parser did not detect audio)"
    if action == "none":
        return "FAIL — no media pipeline ran (not detected as audio)"
    return f"status: action={action}"


def diagnose(phone: str, test_url: str | None = None, test_last_audio: bool = False) -> None:
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

        last_audio_blob = ""
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
                full = m.content or ""
                content = full[:220]
                flags = []
                if m.media_url:
                    flags.append("HAS_MEDIA_URL")
                if full.startswith("🎤"):
                    flags.append("VOICE_TRANSCRIBED")
                elif full.startswith("🎙️"):
                    flags.append("VOICE_FAIL_MSG")
                if "📷" in full:
                    flags.append("IMAGE_ANALYZED")
                if not full.strip():
                    flags.append("EMPTY")
                if full.strip().startswith("{") and "audio" in full:
                    flags.append("RAW_AUDIO_DICT")
                    if not last_audio_blob:
                        last_audio_blob = full
                if full.strip().startswith("{") and "text" in full and "body" in full:
                    flags.append("RAW_TEXT_DICT")
                if full.strip() == "[image]":
                    flags.append("IMAGE_PLACEHOLDER_ONLY")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                print(f"  [{m.created_at}] {m.role}: {content!r}{flag_str}")
                if len(full) > 220:
                    print(f"    (stored length: {len(full)} chars)")

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
        latest_log: dict | None = None
        if not events:
            print("  (none — try sending a test message, then re-run)")
        for i, e in enumerate(events):
            print(f"  [{e.created_at}] {e.event_type or '?'} | status={e.status} | event_id={e.event_id or '-'}")
            if not e.detail:
                continue
            try:
                parsed = json.loads(e.detail)
                if i == 0:
                    latest_log = parsed
                print(f"    VERDICT: {_verdict_from_media_log(parsed)}")
                for key in (
                    "hint",
                    "error",
                    "transcript_preview",
                    "vision_preview",
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
                parse = parsed.get("parse") or {}
                if isinstance(parse, dict) and parse.get("payload_debug"):
                    print(
                        f"    payload_debug: {json.dumps(parse['payload_debug'], ensure_ascii=False)}"
                    )
            except json.JSONDecodeError:
                print(f"    detail: {e.detail[:300]}")

        if latest_log:
            print("\n--- Latest webhook summary ---")
            print(f"  {_verdict_from_media_log(latest_log)}")

        audio_url = test_url
        if not audio_url and (test_last_audio or last_audio_blob):
            audio_url = _find_audio_url_in_text(last_audio_blob)
            if audio_url:
                print(f"\n--- Extracted audio URL from last RAW_AUDIO_DICT message ---")
                print(f"  {audio_url[:120]}{'...' if len(audio_url) > 120 else ''}")
            elif last_audio_blob:
                print("\n--- Could not extract audio URL from stored dict (likely truncated) ---")
                print("  Send a NEW voice note after deploy, or paste full Telnyx media URL with --test-url")

        if audio_url:
            print("\n--- Live download + transcription test ---")
            from app.services import inbound_media

            inbound = InboundMessage(
                channel="whatsapp",
                sender_id=phone,
                is_audio=True,
                media_url=audio_url,
                media_content_type="audio/ogg",
            )
            result, media_log = inbound_media.prepare_inbound_media(db, inbound)
            if isinstance(result, str):
                print(f"  user_message: {result!r}")
            else:
                print(f"  user_text: {result.text!r}")
            print(f"  media_log: {json.dumps(media_log, ensure_ascii=False, indent=2)}")
            print(f"  LIVE VERDICT: {_verdict_from_media_log(media_log)}")

        print("\n--- How to read this ---")
        print("  RAW_AUDIO_DICT     = voice stored as Telnyx dict string (parser/transcribe issue)")
        print("  EMPTY              = webhook had no text (see payload_debug on latest event)")
        print("  VOICE_TRANSCRIBED  = 🎤 prefix means transcription worked")
        print("  Steps: 1) git pull + deploy  2) send NEW voice  3) re-run this script")
        print("  Test download/Whisper only: --test-last-audio or --test-url 'https://...'")
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
    parser.add_argument(
        "--test-last-audio",
        action="store_true",
        help="Extract URL from last RAW_AUDIO_DICT chat message and test download + Whisper",
    )
    args = parser.parse_args()
    diagnose(args.phone, test_url=args.test_url, test_last_audio=args.test_last_audio)


if __name__ == "__main__":
    main()
