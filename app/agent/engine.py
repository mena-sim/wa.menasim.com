from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy import delete, func, select
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
SESSION_RESET_KEYWORD = "menasim"


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


def is_session_reset_command(text: str) -> bool:
    """Typing 'menasim' alone restarts the WhatsApp/chat session."""
    return (text or "").strip().casefold() == SESSION_RESET_KEYWORD


def normalize_user_text(text: str) -> str:
    """Strip voice/screenshot prefixes before routing logic."""
    t = (text or "").strip()
    if t.startswith("🎤"):
        t = t[1:].strip()
    return t


def wants_support_contact(text: str) -> bool:
    """Customer explicitly asks to email/call support or be contacted by a human."""
    t = normalize_user_text(text).casefold()
    if not t:
        return False
    needles = (
        "send email",
        "email support",
        "email to support",
        "contact me",
        "call me",
        "call back",
        "human agent",
        "real person",
        "speak to someone",
        "talk to someone",
        "talk to support",
        "contact support",
        "reach support",
        "need support",
        "support team",
        "ارسل ايميل",
        "أرسل ايميل",
        "ارسل إيميل",
        "أرسل إيميل",
        "ابعت ايميل",
        "ابعت إيميل",
        "ابعت ايميل",
        "ابعث ايميل",
        "ارسل بريد",
        "أرسل بريد",
        "ايميل للسبورت",
        "إيميل للسبورت",
        "ايميل للدعم",
        "إيميل للدعم",
        "تواصل معي",
        "اتصل بي",
        "اتصلوا",
        "يكونوا معي",
        "يككو معي",
        "يخكو معي",
        "موظف",
        "دعم فني",
        "الدعم",
        "بشري",
    )
    if any(n in t for n in needles):
        return True
    if ("ايميل" in t or "إيميل" in t or "email" in t) and (
        "سبورت" in t or "support" in t or "دعم" in t
    ):
        return True
    if ("ابعت" in t or "ابعث" in t or "ارسل" in t or "أرسل" in t) and (
        "ايميل" in t or "إيميل" in t or "email" in t or "بريد" in t
    ):
        return True
    return False


def customer_stated_need(text: str) -> bool:
    """True when the customer already said what they want (not just hi/hello)."""
    t = normalize_user_text(text).casefold()
    if not t:
        return False
    needles = (
        "install",
        "setup",
        "activate",
        "activation",
        "qr",
        "esim",
        "iccid",
        "internet",
        "network",
        "roaming",
        "operator",
        "balance",
        "data left",
        "refund",
        "cancel",
        "order",
        "expired",
        "not work",
        "doesn't work",
        "doesnt work",
        "problem",
        "issue",
        "help me",
        "price",
        "prices",
        "cost",
        "how much",
        "britain",
        "uk",
        "تركيب",
        "ركب",
        "شريحة",
        "تفعيل",
        "شبكة",
        "انترنت",
        "إنترنت",
        "رصيد",
        "باقي",
        "استرجاع",
        "طلب",
        "ما اشتغل",
        "لا يعمل",
        "مشكلة",
        "مساعدة",
        "سعر",
        "أسعار",
        "كم",
        "بريطانيا",
        "برطانيا",
    )
    return any(n in t for n in needles)


def welcome_message(language: str) -> str:
    if language == "ar":
        return "أهلاً! كيف أقدر أساعدك؟"
    return "Hi! How can I help you?"


def clear_conversation_session(db: Session, convo: Conversation) -> None:
    """Delete chat history and reset flags — fresh session for same customer."""
    db.execute(delete(Message).where(Message.conversation_id == convo.id))
    convo.verified = False
    convo.verified_order = ""
    convo.handed_over = False
    convo.needs_human = False
    db.commit()


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
    was_audio_attempt: bool = False,
) -> AgentResult:
    convo = get_or_create_conversation(db, channel, sender_id)

    language = detect_language(text) if text else convo.language or "en"
    convo.language = language

    user_content = normalize_user_text(text)
    if is_image and not user_content:
        user_content = "[image]"

    if is_session_reset_command(user_content):
        clear_conversation_session(db, convo)
        reply = welcome_message(language)
        db.add(
            Message(
                conversation_id=convo.id, role="user", content=user_content, media_url=media_url
            )
        )
        db.add(Message(conversation_id=convo.id, role="assistant", content=reply))
        db.commit()
        return AgentResult(reply=reply, conversation_id=convo.id, language=language)

    is_first_user_message = (
        db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == convo.id, Message.role == "user")
        ).scalar_one()
        or 0
    ) == 0

    # Persist the inbound user message.
    db.add(
        Message(
            conversation_id=convo.id, role="user", content=user_content, media_url=media_url
        )
    )
    db.commit()

    if wants_support_contact(user_content):
        from app.agent.tools import escalate as escalate_tool

        contact = sender_id if channel == "whatsapp" else None
        esc = escalate_tool.run(
            ToolContext(db=db, conversation=convo, language=language),
            reason="Customer asked to contact support",
            summary=user_content,
            contact=contact,
        )
        reply = (
            "تم إرسال طلبك إلى فريق الدعم وسيتواصلون معك قريبًا على واتساب."
            if language == "ar"
            else "I've emailed our support team with your request. They'll contact you on WhatsApp soon."
        )
        db.add(Message(conversation_id=convo.id, role="assistant", content=reply))
        db.commit()
        return AgentResult(
            reply=reply,
            conversation_id=convo.id,
            language=language,
            escalated=esc.get("escalated", True),
            needs_human=True,
        )

    # First message with no stated problem → welcome only. Do not ask device type yet.
    if is_first_user_message and not customer_stated_need(user_content) and not is_image:
        reply = welcome_message(language)
        db.add(Message(conversation_id=convo.id, role="assistant", content=reply))
        db.commit()
        return AgentResult(reply=reply, conversation_id=convo.id, language=language)

    if not user_content.strip() and not is_image and not media_url:
        if was_audio_attempt:
            reply = (
                "🎙️ ما قدرت أقرأ الرسالة الصوتية. جرّب ترسلها مرة ثانية أو اكتب سؤالك نصيًا."
                if language == "ar"
                else "🎙️ I couldn't read that voice note. Please try again or type your question."
            )
        else:
            reply = (
                "ما وصلتني رسالتك. اكتب سؤالك من فضلك."
                if language == "ar"
                else "I didn't receive your message. Please type your question."
            )
        db.add(Message(conversation_id=convo.id, role="assistant", content=reply))
        db.commit()
        return AgentResult(reply=reply, conversation_id=convo.id, language=language)

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
            "\n\n(النظام: ما زالت الصورة غير محللة — اطلب من العميل يعيد إرسالها كصورة.)"
            if language == "ar"
            else "\n\n(System: image was not analyzed — ask the customer to resend it as a photo.)"
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
