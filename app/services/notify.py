from __future__ import annotations

import smtplib
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)


def _smtp_settings(db: Session) -> dict[str, Any]:
    host = runtime_config.get(db, "smtp_host")
    port = runtime_config.get_int(db, "smtp_port", 587)
    user = runtime_config.get(db, "smtp_user")
    password = runtime_config.get(db, "smtp_pass")
    sender = runtime_config.get(db, "smtp_from") or user or "noreply@menasim"
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "sender": sender,
    }


@contextmanager
def smtp_connection(db: Session) -> Iterator[smtplib.SMTP]:
    """Open an authenticated SMTP connection using saved settings."""
    settings = _smtp_settings(db)
    host = settings["host"]
    port = settings["port"]
    if not host:
        raise ValueError("SMTP host is not set.")

    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        if port != 25:
            server.starttls()
            server.ehlo()
    try:
        if settings["user"]:
            server.login(settings["user"], settings["password"])
        yield server
    finally:
        try:
            server.quit()
        except Exception:
            server.close()


def test_smtp_connection(db: Session) -> tuple[bool, str]:
    """Verify SMTP host, TLS, and login without sending mail."""
    settings = _smtp_settings(db)
    if not settings["host"]:
        return False, "SMTP host is not set."
    try:
        with smtp_connection(db) as server:
            server.noop()
        user_part = f" as {settings['user']}" if settings["user"] else ""
        return True, f"Connected to {settings['host']}:{settings['port']}{user_part}."
    except Exception as exc:
        logger.warning("[notify] SMTP connection test failed: %s", exc)
        return False, str(exc)


def send_email(
    db: Session,
    *,
    subject: str,
    body: str,
    to: str | None = None,
) -> tuple[bool, str]:
    """Send a plain-text email via SMTP."""
    settings = _smtp_settings(db)
    recipient = (to or runtime_config.get(db, "alert_email_to") or "").strip()
    if not settings["host"]:
        return False, "SMTP host is not set."
    if not recipient:
        return False, "Alert recipient is not set."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings["sender"]
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtp_connection(db) as server:
            server.send_message(msg)
        logger.info("[notify] email sent to %s subject=%s", recipient, subject)
        return True, f"Email sent to {recipient}."
    except Exception as exc:
        logger.warning("[notify] failed to send email: %s", exc)
        return False, str(exc)


def send_test_email(db: Session, to: str | None = None) -> tuple[bool, str]:
    """Send a test email to confirm SMTP delivery."""
    recipient = (to or runtime_config.get(db, "alert_email_to") or "").strip()
    body = (
        "This is a test email from menasim WA support.\n\n"
        "If you received this, SMTP escalation alerts are configured correctly."
    )
    return send_email(
        db,
        subject="[menasim support] SMTP test",
        body=body,
        to=recipient,
    )


def send_escalation_email(db: Session, subject: str, body: str) -> bool:
    """Send an escalation alert via SMTP. No-op (logs only) if SMTP not configured."""
    if not runtime_config.smtp_enabled(db):
        logger.info("[notify] SMTP not configured; escalation alert not emailed. subject=%s", subject)
        return False

    ok, _msg = send_email(
        db,
        subject=f"[menasim support] {subject}",
        body=body,
    )
    return ok
