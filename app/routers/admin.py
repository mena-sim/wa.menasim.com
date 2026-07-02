from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import admin_auth
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.kb_document import KbDocument
from app.models.message import Message
from app.services import runtime_config, notify
from app.services.kb import distill, docs, ingest, store
from app.services.kb.docs import KbDocError
from app.services.providers.woocommerce import WooCommerceClient
from app.core.config import get_settings
from app.services.telnyx_client import send_whatsapp
from app.services import telnyx_diagnostics

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

CONFIG_GROUPS = ("deepseek", "telnyx", "whatsapp", "wordpress", "agent", "voice", "smtp")


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(payload: LoginIn) -> dict:
    if not admin_auth.verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": admin_auth.issue_token()}


@router.post("/logout")
def logout(token: str = Depends(admin_auth.require_admin)) -> dict:
    admin_auth.revoke(token)
    return {"ok": True}


@router.get("/me")
def me(_: str = Depends(admin_auth.require_admin)) -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- config
@router.get("/config")
def get_config(db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)) -> dict:
    groups = {g: runtime_config.group_view(db, g) for g in CONFIG_GROUPS}
    return {
        "groups": groups,
        "status": {
            "llm_enabled": runtime_config.llm_enabled(db),
            "whatsapp_enabled": runtime_config.whatsapp_enabled(db),
            "woocommerce_enabled": runtime_config.woocommerce_enabled(db),
            "smtp_enabled": runtime_config.smtp_enabled(db),
            "webhook_url": telnyx_diagnostics.webhook_url(),
            "public_base_url": get_settings().public_base_url,
        },
    }


class ConfigIn(BaseModel):
    values: dict[str, Any]


@router.put("/config/{group}")
def put_config(
    group: str,
    payload: ConfigIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    if group not in CONFIG_GROUPS:
        raise HTTPException(status_code=404, detail="Unknown config group")
    runtime_config.set_group(db, group, payload.values)
    return {"ok": True, "group": group, "values": runtime_config.group_view(db, group)}


@router.post("/config/test/{group}")
def test_config(
    group: str,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    if group in ("deepseek",):
        return _test_deepseek(db)
    if group in ("telnyx",):
        return _test_telnyx(db)
    if group in ("whatsapp",):
        return telnyx_diagnostics.test_connection(db)
    if group == "wordpress":
        ok, msg = WooCommerceClient(db).test_connection()
        return {"ok": ok, "message": msg}
    if group == "voice":
        return _test_deepinfra(db)
    if group == "smtp":
        ok, msg = _test_smtp(db)
        return {"ok": ok, "message": msg}
    raise HTTPException(status_code=400, detail="No connection test for this group")


def _test_deepinfra(db: Session) -> dict:
    key = runtime_config.get(db, "deepinfra_api_key")
    if not key:
        return {"ok": False, "message": "DeepInfra API key is not set."}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                "https://api.deepinfra.com/v1/openai/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code < 300:
            return {"ok": True, "message": "DeepInfra API key is valid."}
        return {"ok": False, "message": f"HTTP {resp.status_code}: check the API key."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}


def _test_deepseek(db: Session) -> dict:
    from app.agent import llm

    cfg = runtime_config.llm_config(db)
    if not cfg["api_key"]:
        return {"ok": False, "message": "DeepSeek API key is not set."}
    try:
        msg = llm.chat(
            [{"role": "user", "content": "ping"}],
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=cfg["model"],
            max_tokens=5,
            temperature=0,
        )
        _ = msg.content
        return {"ok": True, "message": f"Connected ({cfg['model']})."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Failed: {exc}"}


def _test_telnyx(db: Session) -> dict:
    api_key = runtime_config.get(db, "telnyx_api_key")
    if not api_key:
        return {"ok": False, "message": "Telnyx API key is not set."}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                "https://api.telnyx.com/v2/messaging_profiles",
                params={"page[size]": 1},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code < 300:
            return {"ok": True, "message": "Telnyx API key is valid."}
        return {"ok": False, "message": f"HTTP {resp.status_code}: check the API key."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}


def _test_smtp(db: Session) -> tuple[bool, str]:
    return notify.test_smtp_connection(db)


class SmtpTestSendIn(BaseModel):
    to: str = ""


@router.post("/smtp/test-send")
def smtp_test_send(
    payload: SmtpTestSendIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    ok, msg = notify.send_test_email(db, to=payload.to or None)
    return {"ok": ok, "message": msg}


class WhatsAppTestSendIn(BaseModel):
    to: str
    text: str = (
        "Test from menasim WA support. If you see this, outbound WhatsApp via Telnyx is working."
    )


class WhatsAppTestInboundIn(BaseModel):
    from_number: str
    text: str = "Hello, I need help with my eSIM."
    send_reply: bool = False


@router.post("/whatsapp/auto-configure")
def whatsapp_auto_configure(
    db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)
) -> dict:
    return telnyx_diagnostics.auto_configure(db)


@router.get("/whatsapp/status")
def whatsapp_status(
    db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)
) -> dict:
    return telnyx_diagnostics.status(db)


@router.post("/whatsapp/test-send")
def whatsapp_test_send(
    payload: WhatsAppTestSendIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    return telnyx_diagnostics.test_send(db, payload.to, payload.text)


@router.post("/whatsapp/test-inbound")
def whatsapp_test_inbound(
    payload: WhatsAppTestInboundIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    return telnyx_diagnostics.test_inbound(
        db,
        from_number=payload.from_number,
        text=payload.text,
        send_reply=payload.send_reply,
    )


# ---------------------------------------------------------------- conversations
def _status_of(convo: Conversation) -> str:
    if convo.closed:
        return "closed"
    if convo.handed_over:
        return "handoff"
    if convo.needs_human:
        return "waiting"
    return "live"


@router.get("/conversations")
def list_conversations(
    filter: str = "all",
    channel: str = "whatsapp",
    q: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    rows = (
        db.execute(select(Conversation).order_by(Conversation.updated_at.desc()).limit(300))
        .scalars()
        .all()
    )
    out = []
    for c in rows:
        status = _status_of(c)
        if filter and filter != "all" and status != filter:
            continue
        if channel and channel != "all" and c.channel != channel:
            continue
        last = db.execute(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(Message.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        last_text = (last.content if last else "") or ""
        name = c.customer_name or c.sender_id
        if q and q.lower() not in f"{name} {c.sender_id} {last_text}".lower():
            continue
        out.append(
            {
                "id": c.id,
                "name": name,
                "sender_id": c.sender_id,
                "channel": c.channel,
                "status": status,
                "handed_over": c.handed_over,
                "needs_human": c.needs_human,
                "last": last_text[:120],
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )
    return {"conversations": out}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.id.asc())
        )
        .scalars()
        .all()
    )
    return {
        "conversation": {
            "id": convo.id,
            "name": convo.customer_name or convo.sender_id,
            "sender_id": convo.sender_id,
            "channel": convo.channel,
            "language": convo.language,
            "status": _status_of(convo),
            "handed_over": convo.handed_over,
            "needs_human": convo.needs_human,
        },
        "messages": [
            {
                "id": m.id,
                "from": ("human" if (m.role == "assistant" and m.is_human) else ("agent" if m.role == "assistant" else "user")),
                "content": m.content,
                "media_url": m.media_url,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }


class HandoverIn(BaseModel):
    handed_over: bool


@router.post("/conversations/{conversation_id}/handover")
def set_handover(
    conversation_id: int,
    payload: HandoverIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.handed_over = payload.handed_over
    db.commit()
    return {"ok": True, "handed_over": convo.handed_over, "status": _status_of(convo)}


class ReplyIn(BaseModel):
    text: str


@router.post("/conversations/{conversation_id}/reply")
def manual_reply(
    conversation_id: int,
    payload: ReplyIn,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty reply")

    # Sending manually implies a human is handling this chat -> pause the AI.
    convo.handed_over = True
    db.add(
        Message(conversation_id=convo.id, role="assistant", content=text, is_human=True)
    )
    db.commit()

    delivery: dict[str, Any] = {"channel": convo.channel}
    if convo.channel == "whatsapp":
        delivery.update(send_whatsapp(db, convo.sender_id, text))
    else:
        delivery["note"] = "Web chat reply stored; the widget will show it on next poll."
    return {"ok": True, "delivery": delivery, "status": _status_of(convo)}


@router.post("/conversations/{conversation_id}/close")
def close_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.closed = True
    convo.handed_over = False
    convo.needs_human = False
    db.commit()
    return {"ok": True, "status": _status_of(convo)}


# ---------------------------------------------------------------- knowledge base
@router.get("/kb")
def list_kb(db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)) -> dict:
    rows = db.execute(select(KbDocument).order_by(KbDocument.id.desc())).scalars().all()
    return {
        "chunks": store.count(),
        "documents": [
            {
                "id": d.id,
                "source": d.source,
                "language": d.language,
                "title": d.title,
                "chunk_count": d.chunk_count,
            }
            for d in rows
        ],
    }


@router.post("/kb/reindex")
def reindex_kb(db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)) -> dict:
    result = ingest.reindex(db)
    return {"ok": True, **result}


@router.post("/kb/upload")
async def upload_kb(
    language: str = "en",
    file: UploadFile | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")
    lang = language if language in ("en", "ar") else "en"
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Only UTF-8 text files (.md, .txt) are supported for now.",
        )
    name = (file.filename or "upload").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] or "doc"
    dest_dir = ingest.KB_ROOT / lang
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.md"
    dest.write_text(text, encoding="utf-8")
    result = ingest.reindex(db)
    return {"ok": True, "saved": f"{lang}/{dest.name}", **result}


# ---- Manual FAQ / KB document editing ----
@router.get("/kb/doc")
def get_kb_doc(
    source: str, db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)
) -> dict:
    try:
        return {"source": source, "body": docs.read_doc(source)}
    except KbDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class KbDocCreate(BaseModel):
    title: str
    language: str = "en"
    body: str = ""


@router.post("/kb/doc")
def create_kb_doc(
    payload: KbDocCreate,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    try:
        return {"ok": True, **docs.create_doc(db, title=payload.title, language=payload.language, body=payload.body)}
    except KbDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class KbDocSave(BaseModel):
    source: str
    body: str


@router.put("/kb/doc")
def save_kb_doc(
    payload: KbDocSave,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    try:
        return {"ok": True, **docs.save_doc(db, source=payload.source, body=payload.body)}
    except KbDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/kb/doc")
def delete_kb_doc(
    source: str, db: Session = Depends(get_db), _: str = Depends(admin_auth.require_admin)
) -> dict:
    try:
        return {"ok": True, **docs.delete_doc(db, source=source)}
    except KbDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- Import WhatsApp chat export (.txt) ----
@router.post("/kb/import-whatsapp")
async def import_whatsapp(
    support_name: str = Form(""),
    language: str = Form("auto"),
    distill_with_ai: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    from app.services.kb import whatsapp_import

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Could not decode the .txt export (expected UTF-8).")
    result = whatsapp_import.import_export(
        db,
        raw=text,
        support_name=support_name,
        language=language,
        distill_with_ai=distill_with_ai,
    )
    return {"ok": True, **result}


@router.post("/conversations/{conversation_id}/save-to-kb")
def save_conversation_to_kb(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(admin_auth.require_admin),
) -> dict:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not runtime_config.llm_enabled(db):
        raise HTTPException(status_code=400, detail="DeepSeek is not configured; cannot distill.")
    msgs = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.id.asc())
        )
        .scalars()
        .all()
    )
    transcript = "\n".join(
        f"{'Customer' if m.role == 'user' else 'Agent'}: {(m.content or '').strip()}"
        for m in msgs
        if (m.content or "").strip()
    )
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Conversation has no usable messages.")
    lang = convo.language if convo.language in ("en", "ar") else "en"
    body = distill.distill_to_faq(db, transcript, lang)
    if not body:
        raise HTTPException(status_code=502, detail="Distillation produced nothing (LLM error or no reusable content).")
    result = docs.append_entry(db, language=lang, filename="captured.md", markdown=body)
    return {"ok": True, **result}
