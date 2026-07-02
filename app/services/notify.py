from __future__ import annotations

import re
import smtplib
import ssl
from contextlib import contextmanager
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)

_EMAIL_ONLY_RE = re.compile(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")


def _email_only(value: str) -> str:
    """SMTP envelope addresses must be plain ASCII emails."""
    text = (value or "").strip()
    match = _EMAIL_ONLY_RE.search(text)
    if match:
        return match.group(1)
    _, addr = parseaddr(text)
    return (addr or text).strip()


def _encode_address(value: str) -> str:
    """Encode display names in From/To when they contain non-ASCII characters."""
    value = (value or "").strip()
    if not value:
        return ""
    name, addr = parseaddr(value)
    if not addr:
        addr = _email_only(value) or value
    if name:
        try:
            name.encode("ascii")
            return formataddr((name, addr))
        except UnicodeEncodeError:
            return formataddr((str(Header(name, "utf-8")), addr))
    return addr


def _build_message(*, subject: str, body: str, sender: str, recipient: str) -> MIMEText:
    """Build a UTF-8 email; headers use RFC 2047, body uses base64 CTE when needed."""
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["Subject"] = Header(subject or "", "utf-8")
    msg["From"] = _encode_address(sender)
    msg["To"] = _encode_address(recipient)
    return msg


def _message_bytes(msg: MIMEText) -> bytes:
    """Serialize for SMTP; verify header lines are ASCII-safe on the wire."""
    raw = msg.as_bytes()
    for line in raw.split(b"\r\n"):
        if not line:
            break
        if line.startswith(
            (b"Subject:", b"From:", b"To:", b"Cc:", b"Bcc:", b"Content-Type:", b"MIME-Version:")
        ):
            line.decode("ascii")
    return raw


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


def _smtp_mode(port: int) -> str:
    if port == 465:
        return "SSL"
    if port == 25:
        return "plain"
    return "STARTTLS"


@contextmanager
def smtp_connection(db: Session) -> Iterator[smtplib.SMTP]:
    """Open an authenticated SMTP connection using saved settings."""
    settings = _smtp_settings(db)
    host = settings["host"]
    port = settings["port"]
    if not host:
        raise ValueError("SMTP host is not set.")

    if port == 465:
        context = ssl.create_default_context()
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        server.ehlo()
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        if port != 25:
            server.starttls(context=ssl.create_default_context())
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
        mode = _smtp_mode(settings["port"])
        return True, f"Connected to {settings['host']}:{settings['port']} ({mode}){user_part}."
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

    msg = _build_message(
        subject=subject,
        body=body,
        sender=settings["sender"],
        recipient=recipient,
    )
    payload = _message_bytes(msg)
    from_addr = _email_only(settings["sender"])
    to_addr = _email_only(recipient)

    try:
        with smtp_connection(db) as server:
            server.sendmail(from_addr, [to_addr], payload)
        logger.info("[notify] email sent to %s", to_addr)
        return True, f"Email sent to {recipient}."
    except Exception as exc:
        logger.warning("[notify] failed to send email to %s: %s", to_addr, exc)
        return False, str(exc)


def send_test_email(db: Session, to: str | None = None) -> tuple[bool, str]:
    """Send a test email to confirm SMTP delivery."""
    body = (
        "This is a test email from menasim WA support.\n\n"
        "If you received this, SMTP escalation alerts are configured correctly."
    )
    return send_email(
        db,
        subject="[menasim support] SMTP test",
        body=body,
        to=to,
    )


def send_escalation_email(db: Session, subject: str, body: str) -> bool:
    """Send an escalation alert via SMTP. No-op (logs only) if SMTP not configured."""
    if not runtime_config.smtp_enabled(db):
        logger.info("[notify] SMTP not configured; escalation alert not emailed")
        return False

    ok, _msg = send_email(
        db,
        subject=f"[menasim support] {subject}",
        body=body,
    )
    return ok
