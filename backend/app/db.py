"""SQLAlchemy 2 engine/session/Base wiring (SQLite).

The engine is built lazily from ``get_settings().bigi_db`` so tests can
re-point ``BIGI_DB`` at a temp file (clear the settings cache, call
``reset_engine()``, then ``init_db()``).
"""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    url = get_settings().bigi_db
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _SessionFactory


def SessionLocal() -> Session:
    """Create a new ORM session bound to the current engine."""
    return _factory()()


def reset_engine() -> None:
    """Drop the cached engine/session factory (tests re-point BIGI_DB)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def init_db() -> None:
    """Create all tables. Imports models so they register on Base.metadata."""
    from . import models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(bind=get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
