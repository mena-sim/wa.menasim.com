from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import llm
from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolContext, execute_tool
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message
from app.services import runtime_config

logger = get_logger(__name__)

_HISTORY_LIMIT = 12  # prior turns fed to the model
_rate_state: dict[str, deque[float]] = defaultdict(deque)


@dataclass
class AgentResult:
    reply: str
    conversation_id: int
    language: str
    media_url: str | None = None
    escalated: bool = False
    needs_human: bool = False
    suppressed: bool = False  # True when AI stayed silent (human handover)


def detect_language(text: str) -> str:
    for ch in text or "":
        if "\u0600" <= ch <= "\u06ff":  # Arabic block
            return "ar"
    return "en"


def _rate_limited(db: Session, sender_id: str) -> bool:
    limit = runtime_config.get_int(db, "rate_limit_per_minute", 20)
    if limit <= 0:
        return False
    now = time.time()
    q = _rate_state[sender_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def get_or_create_conversation(db: Session, channel: str, sender_id: str) -> Conversation:
    convo = db.execute(
        select(Conversation).where(
            Conversation.channel == channel, Conversation.sender_id == sender_id
        )
    ).scalar_one_or_none()
    if convo is None:
        convo = Conversation(channel=channel, sender_id=sender_id, language="en")
        db.add(convo)
        db.commit()
        db.refresh(convo)
    return convo


def _load_history(db: Session, conversation_id: int) -> list[dict]:
    rows = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.id.desc())
            .limit(_HISTORY_LIMIT)
        )
        .scalars()
        .all()
    )
    rows.reverse()
    return [{"role": m.role, "content": m.content or ""} for m in rows]


def _tool_calls_to_dicts(tool_calls) -> list[dict]:
    out = []
    for tc in tool_calls:
        out.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
        )
    return out


def handle_message(
    db: Session,
    *,
    channel: str,
    sender_id: str,
    text: str,
    media_url: str | None = None,
    is_image: bool = False,
) -> AgentResult:
    convo = get_or_create_conversation(db, channel, sender_id)

    language = detect_language(text) if text else convo.language or "en"
    convo.language = language

    # Persist the inbound user message.
    user_content = text or ""
    if is_image and not user_content:
        user_content = "[image]"
    db.add(
        Message(
            conversation_id=convo.id, role="user", content=user_content, media_url=media_url
        )
    )
    db.commit()

    # Human handover: agent stays silent so a human can reply from the console.
    if convo.handed_over:
        db.refresh(convo)
        return AgentResult(
            reply="",
            conversation_id=convo.id,
            language=language,
            needs_human=convo.needs_human,
            suppressed=True,
        )

    if _rate_limited(db, sender_id):
        msg = (
            "أنت ترسل رسائل بسرعة كبيرة. أمهلني لحظة من فضلك."
            if language == "ar"
            else "You're sending messages very quickly. Please give me a moment."
        )
        db.add(Message(conversation_id=convo.id, role="assistant", content=msg))
        db.commit()
        return AgentResult(reply=msg, conversation_id=convo.id, language=language)

    if not runtime_config.llm_enabled(db):
        msg = (
            "المساعد غير مُهيأ بعد (DEEPSEEK_API_KEY مفقود)."
            if language == "ar"
            else "The assistant is not configured yet (missing DEEPSEEK_API_KEY)."
        )
        db.add(Message(conversation_id=convo.id, role="assistant", content=msg))
        db.commit()
        return AgentResult(reply=msg, conversation_id=convo.id, language=language)

    llm_cfg = runtime_config.llm_config(db)

    # Build the working message list.
    system_prompt = build_system_prompt(
        language,
        whatsapp=(channel == "whatsapp"),
        agent_name=runtime_config.get(db, "agent_name"),
        tone=runtime_config.get(db, "agent_tone"),
        extra_instructions=runtime_config.get(db, "agent_system_instructions"),
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(_load_history(db, convo.id)[:-1] or [])  # history excluding the just-added msg
    user_text = user_content
    if is_image:
        note = (
            "\n\n(النظام: أرسل العميل صورة/لقطة شاشة لا يمكنك رؤيتها. اطلب وصفًا نصيًا أو صعّد للدعم.)"
            if language == "ar"
            else "\n\n(System: the customer sent an image/screenshot you cannot see. Ask them to "
            "describe it in text, or escalate to a human.)"
        )
        user_text = f"{user_content}{note}"
    messages.append({"role": "user", "content": user_text})

    ctx = ToolContext(db=db, conversation=convo, language=language)

    max_iters = max(1, runtime_config.get_int(db, "agent_max_tool_iters", 6))
    final_text = ""
    for _ in range(max_iters):
        message = llm.chat(messages, tools=TOOL_SCHEMAS, **llm_cfg)
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            final_text = (message.content or "").strip()
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": _tool_calls_to_dicts(tool_calls),
            }
        )
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(tc.function.name, args, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    else:
        # Ran out of iterations; ask the model for a final answer with no tools.
        message = llm.chat(messages, tools=None, **llm_cfg)
        final_text = (message.content or "").strip()

    if not final_text:
        final_text = (
            "عذرًا، لم أتمكن من تكوين رد. سأصعّد طلبك إلى الدعم."
            if language == "ar"
            else "Sorry, I couldn't compose a reply. I'll pass this to a human agent."
        )

    db.add(
        Message(
            conversation_id=convo.id,
            role="assistant",
            content=final_text,
            media_url=ctx.media_url,
        )
    )
    db.commit()
    db.refresh(convo)

    return AgentResult(
        reply=final_text,
        conversation_id=convo.id,
        language=language,
        media_url=ctx.media_url,
        escalated=ctx.escalated,
        needs_human=convo.needs_human,
    )
