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


def test_telnyx_webhook_skips_without_public_key():
    assert verify_webhook(b"{}", signature_header=None, timestamp_header=None) is True


def test_woocommerce_disabled_by_default():
    assert WooCommerceClient().enabled is False


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
