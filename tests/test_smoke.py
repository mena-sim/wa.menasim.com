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
        "esim_status",
        "resend_qr",
        "escalate",
        "refund_ticket",
    } <= names


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
        db, channel="web", sender_id="tester", text="hello"
    )
    assert result.conversation_id > 0
    assert "not configured" in result.reply.lower() or "DEEPSEEK" in result.reply


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


def test_parse_inbound_accepts_whatsapp_string_from():
    """Telnyx WhatsApp webhooks send `from` as a plain E.164 string."""
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
