from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)


def send_escalation_email(db: Session, subject: str, body: str) -> bool:
    """Send an escalation alert via SMTP. No-op (logs only) if SMTP not configured."""
    if not runtime_config.smtp_enabled(db):
        logger.info("[notify] SMTP not configured; escalation alert not emailed. subject=%s", subject)
        return False

    host = runtime_config.get(db, "smtp_host")
    port = runtime_config.get_int(db, "smtp_port", 587)
    user = runtime_config.get(db, "smtp_user")
    password = runtime_config.get(db, "smtp_pass")
    sender = runtime_config.get(db, "smtp_from") or user or "noreply@menasim"
    to = runtime_config.get(db, "alert_email_to")

    msg = EmailMessage()
    msg["Subject"] = f"[menasim support] {subject}"
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        logger.info("[notify] escalation email sent to %s", to)
        return True
    except Exception as exc:
        logger.warning("[notify] failed to send escalation email: %s", exc)
        return False
