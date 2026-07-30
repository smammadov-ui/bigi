"""Webhook route tests (offline).

``run_pipeline`` is monkeypatched in the webhook router so the request never
touches BO/LLM — we capture the ``raw_text`` it would have run and assert the
secret enforcement, ADF flatten, plain-string and text/plain extraction.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.routers import webhook as webhook_router


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_run(db, raw_text, company_uuid=None):
        seen["raw_text"] = raw_text
        return {"parsed": {}, "account": {}, "seizure_check": {}, "declaration": {}}

    monkeypatch.setattr(webhook_router, "run_pipeline", fake_run)
    return seen


def _adf(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def test_webhook_adf_description(client, captured):
    payload = {"issue": {"fields": {"description": _adf("Hallo Welt")}}}
    resp = client.post("/api/webhook/jira", json=payload)
    assert resp.status_code == 200
    assert captured["raw_text"] == "Hallo Welt"


def test_webhook_plain_string_description(client, captured):
    payload = {"issue": {"fields": {"description": "just a string"}}}
    resp = client.post("/api/webhook/jira", json=payload)
    assert resp.status_code == 200
    assert captured["raw_text"] == "just a string"


def test_webhook_text_plain_body(client, captured):
    resp = client.post(
        "/api/webhook/jira",
        content="raw seizure text",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert captured["raw_text"] == "raw seizure text"


def test_webhook_empty_description_400(client, captured):
    payload = {"issue": {"fields": {}}}
    resp = client.post("/api/webhook/jira", json=payload)
    assert resp.status_code == 400


def test_webhook_secret_enforced(client, captured, monkeypatch):
    # Configure a secret and bust the cached Settings.
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", "s3cr3t")
    get_settings.cache_clear()
    try:
        payload = {"issue": {"fields": {"description": "x"}}}

        # missing secret -> 401
        assert client.post("/api/webhook/jira", json=payload).status_code == 401

        # wrong secret -> 401
        bad = client.post(
            "/api/webhook/jira", json=payload, headers={"X-Webhook-Secret": "nope"}
        )
        assert bad.status_code == 401

        # correct via header -> ok
        ok = client.post(
            "/api/webhook/jira", json=payload, headers={"X-Webhook-Secret": "s3cr3t"}
        )
        assert ok.status_code == 200

        # correct via query param -> ok
        ok2 = client.post("/api/webhook/jira?secret=s3cr3t", json=payload)
        assert ok2.status_code == 200
    finally:
        monkeypatch.delenv("JIRA_WEBHOOK_SECRET", raising=False)
        get_settings.cache_clear()


def test_webhook_no_secret_allows(client, captured):
    # Default test settings have no secret -> request goes through.
    get_settings.cache_clear()
    payload = {"issue": {"fields": {"description": "x"}}}
    resp = client.post("/api/webhook/jira", json=payload)
    assert resp.status_code == 200
