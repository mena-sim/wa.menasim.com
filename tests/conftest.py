from __future__ import annotations

import os
import tempfile

# Configure an isolated test environment BEFORE importing any app module.
_TMP = tempfile.mkdtemp(prefix="menasim_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["CHROMA_DIR"] = os.path.join(_TMP, "chroma")
os.environ["DEEPSEEK_API_KEY"] = ""  # keep LLM disabled for offline tests
os.environ["WC_BASE_URL"] = ""
os.environ["TELNYX_API_KEY"] = ""
os.environ["TELNYX_WEBHOOK_PUBLIC_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_WHATSAPP_FROM"] = ""
os.environ["TWILIO_SMS_FROM"] = ""

import pytest  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: F401,E402  (populate metadata)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
