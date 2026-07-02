from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# Ensure sqlite folder exists
if settings.database_url.startswith("sqlite:///"):
    db_path = settings.database_url.replace("sqlite:///", "", 1)
    db_dir = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(db_dir, exist_ok=True)

_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so metadata is populated before create_all
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


# Columns added after the first release. SQLite create_all won't ALTER existing
# tables, so add any missing columns idempotently (prototype-friendly; no Alembic).
_EXTRA_COLUMNS: dict[str, dict[str, str]] = {
    "conversations": {
        "handed_over": "BOOLEAN DEFAULT 0",
        "closed": "BOOLEAN DEFAULT 0",
        "customer_name": "VARCHAR(128) DEFAULT ''",
    },
    "messages": {
        "is_human": "BOOLEAN DEFAULT 0",
    },
}


def _ensure_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _EXTRA_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
