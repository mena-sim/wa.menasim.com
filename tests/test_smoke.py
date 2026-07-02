from __future__ import annotations

from app.agent import engine
from app.agent.tools import TOOL_SCHEMAS, ToolContext, execute_tool
from app.services.channels.whatsapp_telnyx_channel import parse_inbound
from app.services.kb.ingest import _chunk_markdown
from app.services.providers import registry
from app.services.providers.woocommerce import WooCommerceClient
from app.services.telnyx_client import verify_webhook


def test_language_detection():
    assert engine.detect_language("How do I install my eSIM?") == "en"
    assert engine.detect_language("كيف أركب الشريحة؟") == "ar"


def test_tool_schemas_exposed():
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert {
        "kb_search",
        "order_lookup",
        "check_esim_usage",
        "esim_status",
        "resend_qr",
        "escalate",
        "refund_ticket",
    } <= names


def test_check_esim_usage_parses_html():
    from app.agent.tools.check_esim_usage import _parse_usage_html

    html = (
        "<table><tr><td>remaining</td><td>1500</td></tr>"
        "<tr><td>total</td><td>5000</td></tr>"
        "<tr><td>status</td><td>ACTIVE</td></tr></table>"
    )
    rows = _parse_usage_html(html)
    assert rows["remaining"] == "1500"
    assert rows["status"] == "ACTIVE"


def test_check_esim_usage_requires_verification(db):
    from app.agent.tools import ToolContext, execute_tool
    from app.models.conversation import Conversation

    convo = Conversation(channel="whatsapp", sender_id="+100", language="en")
    db.add(convo)
    db.commit()
    ctx = ToolContext(db=db, conversation=convo, language="en")
    result = execute_tool("check_esim_usage", {"iccid": "8931086826051301003"}, ctx)
    assert result.get("verified") is False


def test_telnyx_webhook_skips_without_public_key(db):
    assert verify_webhook(db, b"{}", signature_header=None, timestamp_header=None) is True


def test_woocommerce_disabled_by_default(db):
    assert WooCommerceClient(db).enabled is False


def test_registry_no_providers(db):
    assert registry.get_provider(db) is None
    assert "airalo" in registry.available_provider_names()


def test_chunk_markdown_splits_on_headings():
    text = "# A\nalpha\n\n# B\nbeta\n\n# C\ngamma"
    chunks = _chunk_markdown(text, max_chars=20)
    assert len(chunks) >= 2
    assert any("alpha" in c for c in chunks)


def test_engine_without_llm_returns_message(db):
    result = engine.handle_message(
        db, channel="web", sender_id="tester", text="I need help installing my eSIM"
    )
    assert result.conversation_id > 0
    assert "not configured" in result.reply.lower() or "DEEPSEEK" in result.reply


def test_first_message_welcome_only(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.runtime_config.llm_enabled",
        lambda _db: True,
    )

    def _fake_chat(*_args, **_kwargs):
        raise AssertionError("LLM should not be called for vague first message")

    monkeypatch.setattr("app.agent.llm.chat", _fake_chat)

    result = engine.handle_message(db, channel="whatsapp", sender_id="+447700900001", text="مرحباً")
    assert "كيف أقدر أساعدك" in result.reply
    assert "iphone" not in result.reply.lower()
    assert "android" not in result.reply.lower()


def test_session_reset_on_menasim_keyword(db, monkeypatch):
    monkeypatch.setattr("app.services.runtime_config.llm_enabled", lambda _db: True)
    monkeypatch.setattr(
        "app.agent.llm.chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no llm on reset")),
    )

    engine.handle_message(db, channel="whatsapp", sender_id="+447700900002", text="hello there")
    result = engine.handle_message(db, channel="whatsapp", sender_id="+447700900002", text="menasim")
    assert result.reply in ("Hi! How can I help you?", "أهلاً! كيف أقدر أساعدك؟")

    from sqlalchemy import select
    from app.models.message import Message

    msgs = db.execute(
        select(Message).where(Message.conversation_id == result.conversation_id)
    ).scalars().all()
    assert len(msgs) == 2
    assert msgs[0].content == "menasim"
    assert msgs[1].role == "assistant"


def test_escalate_tool_creates_ticket(db):
    convo = engine.get_or_create_conversation(db, "web", "esc-tester")
    ctx = ToolContext(db=db, conversation=convo, language="en")
    result = execute_tool("escalate", {"reason": "cannot resolve"}, ctx)
    assert result["escalated"] is True
    db.refresh(convo)
    assert convo.needs_human is True


def test_parse_inbound_ignores_delivery_events():
    body = {"data": {"event_type": "message.sent", "payload": {}}}
    assert parse_inbound(body) is None


def test_parse_inbound_extracts_whatsapp_text():
    body = {
        "data": {
            "event_type": "message.received",
            "id": "evt_1",
            "payload": {
                "id": "msg_1",
                "from": {"phone_number": "+15550001111"},
                "text": "hi",
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.sender_id == "+15550001111"
    assert inbound.text == "hi"
    assert inbound.event_id == "msg_1"


def test_parse_inbound_extracts_audio_url_from_broken_text_blob():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    # Truncated dict strings (unclosed quote) happen in Telnyx payload.text — regex should still recover URL.
    text_blob = (
        "{'audio': {'id': '1056246466759040', 'mime_type': 'audio/ogg; codecs=opus', "
        "'sha256': 'abc', 'url': 'https://rcs-outbound.us-central-1.telnyxcloudstorage.com/voice.ogg"
    )
    body = {
        "data": {
            "event_type": "message.received",
            "payload": {"id": "voice-broken", "from": "+447954823445", "text": text_blob},
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_audio is True
    assert "telnyxcloudstorage.com" in (inbound.media_url or "")
    assert "media_from_text_blob" in (inbound.parse_debug or {}).get("parse_notes", [])


def test_parse_inbound_scans_nested_payload_for_audio_url():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "voice-nested",
                "from": "+447954823445",
                "text": "",
                "type": "WHATSAPP",
                "whatsapp_message": {
                    "audio": {
                        "url": "https://rcs-outbound.us-central-1.telnyxcloudstorage.com/a.ogg",
                        "mime_type": "audio/ogg; codecs=opus",
                    }
                },
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_audio is True
    assert inbound.text == ""


def test_classify_whatsapp_voice_note():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "voice-1",
                "from": "+447700900999",
                "whatsapp_message": {"type": "audio", "audio": {"link": "https://media.telnyx.com/v.ogg"}},
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_audio is True
    assert inbound.is_image is False


def test_parse_inbound_telnyx_embedded_audio_dict_in_text():
    """Telnyx sometimes puts the WA audio object in payload.text as a Python dict string."""
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    audio_url = "https://rcs-outbound.us-central-1.telnyxcloudstorage.com/voice.ogg"
    text_blob = (
        "{'audio': {'id': '1056246466759040', "
        "'mime_type': 'audio/ogg; codecs=opus', "
        f"'url': '{audio_url}'}}}}"
    )
    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "voice-2",
                "from": "+447954823445",
                "text": text_blob,
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_audio is True
    assert inbound.media_url == audio_url
    assert inbound.text == ""


def test_parse_inbound_telnyx_embedded_text_dict_in_text():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    text_blob = (
        "{'foreign_id': 'wamid.test', 'from': '+447954823445', "
        "'id': 'fd7e35f9-9bd9-486a-af68-a668e003a743', "
        "'text': {'body': 'مرحبا كيف الحال'}}"
    )
    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "text-1",
                "from": "+447954823445",
                "text": text_blob,
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.text == "مرحبا كيف الحال"
    assert inbound.is_audio is False


def test_parse_inbound_audio_url_field():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "voice-3",
                "from": "+447700900999",
                "whatsapp_message": {
                    "type": "audio",
                    "audio": {
                        "url": "https://rcs-outbound.us-central-1.telnyxcloudstorage.com/a.ogg",
                        "mime_type": "audio/ogg; codecs=opus",
                    },
                },
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_audio is True
    assert "telnyxcloudstorage.com" in (inbound.media_url or "")


def test_classify_whatsapp_document_rejected():
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "doc-1",
                "from": "+447700900999",
                "whatsapp_message": {
                    "type": "document",
                    "document": {"link": "https://media.telnyx.com/f.pdf", "filename": "file.pdf"},
                },
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.is_unsupported_media is True


def test_prepare_inbound_media_transcribes_audio(db, monkeypatch):
    from app.services.channels.base import InboundMessage
    from app.services import inbound_media

    monkeypatch.setattr("app.services.runtime_config.voice_enabled", lambda _db: True)
    monkeypatch.setattr(
        "app.services.inbound_media.fetch_media",
        lambda _db, url: (b"audio-bytes", "audio/ogg"),
    )
    monkeypatch.setattr(
        "app.services.transcription.transcribe",
        lambda _db, audio, **kw: {"text": "كيف أركب الشريحة؟", "language": "ar"},
    )
    inbound = InboundMessage(
        channel="whatsapp",
        sender_id="+1",
        is_audio=True,
        media_url="https://media.telnyx.com/v.ogg",
        media_content_type="audio/ogg",
    )
    out, media_log = inbound_media.prepare_inbound_media(db, inbound)
    assert isinstance(out, InboundMessage)
    assert out.text.startswith("🎤")
    assert "الشريحة" in out.text
    assert out.is_audio is False
    assert media_log.get("action") == "transcribed"


def test_parse_inbound_accepts_whatsapp_string_from():
    """Telnyx WhatsApp webhooks send `from` as a plain E.164 string."""
    from app.services.channels.whatsapp_telnyx_channel import parse_inbound

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "msg_wa_1",
                "from": "+447822002099",
                "text": "hello",
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.sender_id == "+447822002099"
    assert inbound.text == "hello"


def test_whatsapp_payload_uses_telnyx_schema():
    from app.services.telnyx_client import _whatsapp_payload, normalize_e164

    assert normalize_e164("447822002099") == "+447822002099"
    assert _whatsapp_payload("Hi there") == {
        "type": "text",
        "text": {"body": "Hi there", "preview_url": False},
    }
    media = _whatsapp_payload("caption", media_url="https://example.com/qr.png")
    assert media == {
        "type": "image",
        "image": {"link": "https://example.com/qr.png", "caption": "caption"},
    }


def test_parse_inbound_ignores_outbound_direction():
    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "x1",
                "direction": "outbound",
                "from": "+447822002099",
                "text": "should ignore",
            },
        }
    }
    assert parse_inbound(body) is None


def test_parse_inbound_normalizes_whatsapp_prefix():
    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "x2",
                "direction": "inbound",
                "from": "whatsapp:+447700900999",
                "text": "hi",
            },
        }
    }
    inbound = parse_inbound(body)
    assert inbound is not None
    assert inbound.sender_id == "+447700900999"


def test_pick_app_messaging_profile():
    from app.services.telnyx_resolve import (
        MENASIM_MESSAGING_PROFILE_NAME,
        pick_app_messaging_profile,
    )

    profiles = [
        {"id": "vox-id", "name": "Voxbulk", "webhook_url": "https://api.voxbulk.com/telnyx/webhooks/messages"},
        {"id": "sms-id", "name": "SMS", "webhook_url": "https://api.voxbulk.com/telnyx/webhooks/messages"},
        {"id": "wa-id", "name": "WA 2-99", "webhook_url": "https://wa.menasim.com/telnyx/webhooks/messages"},
        {"id": "other-id", "name": "Other", "webhook_url": "https://example.com/hook"},
    ]
    picked = pick_app_messaging_profile(
        profiles,
        configured_profile_id="wa-id",
        app_webhook_url="https://wa.menasim.com/telnyx/webhooks/messages",
    )
    assert picked["id"] == "wa-id"
    assert picked["name"] == MENASIM_MESSAGING_PROFILE_NAME

    picked2 = pick_app_messaging_profile(
        profiles,
        configured_profile_id="",
        app_webhook_url="https://wa.menasim.com/telnyx/webhooks/messages",
    )
    assert picked2["id"] == "wa-id"

    picked3 = pick_app_messaging_profile(
        profiles,
        configured_profile_id="vox-id",
        app_webhook_url="https://wa.menasim.com/telnyx/webhooks/messages",
    )
    assert picked3["id"] == "wa-id"


def test_find_menasim_profile_ignores_other_profiles():
    from app.services.telnyx_resolve import find_menasim_profile

    profiles = [
        {"id": "ai-id", "name": "ai-assistant-c8d58ec9", "webhook_url": ""},
        {"id": "wa-id", "name": "WA 2-99", "webhook_url": "https://wa.menasim.com/telnyx/webhooks/messages"},
    ]
    hit = find_menasim_profile(profiles)
    assert hit is not None
    assert hit["id"] == "wa-id"
    assert find_menasim_profile([{"id": "x", "name": "SMS"}]) is None


def test_whatsapp_inbound_agent_pipeline(db):
    from app.services import telnyx_diagnostics

    result = telnyx_diagnostics.test_inbound(
        db, from_number="+447700900999", text="How do I install my eSIM?", send_reply=False
    )
    assert result["ok"] is True
    assert result["channel"] == "whatsapp"
    assert result["conversation_id"] > 0
    assert result["reply"]


def test_whatsapp_status_flags_business_id_as_profile(db):
    from app.services import runtime_config, telnyx_diagnostics
    from app.services.telnyx_resolve import looks_like_waba_id

    runtime_config.set_value(db, "telnyx_messaging_profile_id", "1339285631627922")
    db.commit()
    assert looks_like_waba_id("1339285631627922")
    st = telnyx_diagnostics.discover(db)
    if runtime_config.get(db, "telnyx_api_key"):
        assert any("WABA ID" in w for w in st["warnings"])


def test_telnyx_resolve_id_formats():
    from app.services.telnyx_resolve import is_uuid, looks_like_waba_id

    assert looks_like_waba_id("1339285631627922")
    assert is_uuid("4e3162a5-13f6-4d12-b246-81705767a0b3")
    assert not looks_like_waba_id("4e3162a5-13f6-4d12-b246-81705767a0b3")


def test_smtp_connection_test(db, monkeypatch):
    from app.services import notify, runtime_config

    runtime_config.set_value(db, "smtp_host", "smtp.example.com")
    runtime_config.set_value(db, "smtp_port", "587")
    runtime_config.set_value(db, "smtp_user", "user@example.com")
    db.commit()

    class FakeSMTP:
        def __init__(self, host, port, timeout=20):
            self.host = host
            self.port = port

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def noop(self):
            return (250, b"OK")

        def quit(self):
            return None

    monkeypatch.setattr("app.services.notify.smtplib.SMTP", FakeSMTP)
    ok, msg = notify.test_smtp_connection(db)
    assert ok is True
    assert "smtp.example.com" in msg


def test_smtp_send_unicode_subject_and_body(db, monkeypatch):
    from app.services import notify, runtime_config

    runtime_config.set_value(db, "smtp_host", "smtp.example.com")
    runtime_config.set_value(db, "smtp_port", "587")
    runtime_config.set_value(db, "smtp_from", "Menasim £ Support <alerts@menasim.com>")
    runtime_config.set_value(db, "alert_email_to", "team@menasim.com")
    db.commit()

    sent: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=20):
            pass

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def sendmail(self, from_addr, to_addrs, message):
            sent["from_addr"] = from_addr
            sent["to_addrs"] = to_addrs
            sent["message"] = message
            assert isinstance(message, (bytes, bytearray))
            message.decode("utf-8")

        def quit(self):
            return None

    monkeypatch.setattr("app.services.notify.smtplib.SMTP", FakeSMTP)
    ok, msg = notify.send_email(
        db,
        subject="[menasim support] Refund £50",
        body="Customer asked about £50 UK eSIM.\nمرحبا",
    )
    assert ok is True
    assert sent["to_addrs"] == ["team@menasim.com"]
    assert b"\xc2\xa3" in sent["message"] or b"=C2=A3" in sent["message"]


def test_smtp_send_test_email(db, monkeypatch):
    from app.services import notify, runtime_config

    runtime_config.set_value(db, "smtp_host", "smtp.example.com")
    runtime_config.set_value(db, "smtp_port", "587")
    runtime_config.set_value(db, "alert_email_to", "alerts@menasim.com")
    db.commit()

    sent: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=20):
            pass

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def sendmail(self, from_addr, to_addrs, message):
            sent["to"] = to_addrs[0]
            sent["message"] = message

        def quit(self):
            return None

    monkeypatch.setattr("app.services.notify.smtplib.SMTP", FakeSMTP)
    ok, msg = notify.send_test_email(db)
    assert ok is True
    assert sent["to"] == "alerts@menasim.com"
    assert b"SMTP test" in sent["message"]

