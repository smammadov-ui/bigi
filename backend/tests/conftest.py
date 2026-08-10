"""Test fixtures: an isolated temp-SQLite DB per test, a TestClient, a Session.

Each test gets a fresh SQLite file (via ``tmp_path``). We point ``BIGI_DB`` at
it, clear the settings cache, rebuild the engine, and create the schema before
yielding either the HTTP ``client`` or a raw ``db`` Session.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.db import SessionLocal, init_db, reset_engine
from app.main import app


_CRED_ENV_VARS = (
    "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
    "BO_BASE_URL", "BO_INTTOKEN", "BO_INTTOKEN_FILE",
    "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_JQL",
)


@pytest.fixture()
def _temp_db(tmp_path, monkeypatch):
    """Point BIGI_DB at a fresh temp file and (re)initialize the engine."""
    db_path = tmp_path / "bigi_test.db"
    monkeypatch.setenv("BIGI_DB", f"sqlite:///{db_path}")
    # Credential fallbacks must not leak from the developer's shell OR the
    # local backend/.env file into the tests. Setting the vars to "" beats
    # both: real env vars are replaced, and (in pydantic-settings precedence)
    # an env var — even empty — overrides a dotenv value.
    for var in _CRED_ENV_VARS:
        monkeypatch.setenv(var, "")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    # Teardown: drop the engine so the next test rebuilds cleanly.
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def client(_temp_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(_temp_db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
