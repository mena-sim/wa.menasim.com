from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def send_escalation_email(subject: str, body: str) -> bool:
    """Send an escalation alert via SMTP. No-op (logs only) if SMTP not configured."""
    s = get_settings()
    if not s.smtp_enabled:
        logger.info("[notify] SMTP not configured; escalation alert not emailed. subject=%s", subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[menasim support] {subject}"
    msg["From"] = s.smtp_from or s.smtp_user or "noreply@menasim"
    msg["To"] = s.alert_email_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:
            server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_pass)
            server.send_message(msg)
        logger.info("[notify] escalation email sent to %s", s.alert_email_to)
        return True
    except Exception as exc:
        logger.warning("[notify] failed to send escalation email: %s", exc)
        return False
