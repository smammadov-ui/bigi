"""Offline tests for the Finom BO client (httpx monkeypatched)."""
from __future__ import annotations

import httpx
import pytest

from app import bo_client
from app.bo_client import BOClient, BOError, is_processing, status_name


class _FakeResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


# --- status helpers ---------------------------------------------------------


def test_status_name_handles_str_and_dict():
    assert status_name("Processing") == "Processing"
    assert status_name({"name": "Processing"}) == "Processing"
    assert status_name(None) == ""
    assert status_name({}) == ""


def test_is_processing():
    assert is_processing({"status": "Processing"}) is True
    assert is_processing({"status": {"name": "Processing"}}) is True
    assert is_processing({"status": "Closed"}) is False
    assert is_processing({}) is False


# --- cstools_search ---------------------------------------------------------


def test_cstools_search_sends_cookie_url_body(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResp(200, {"items": [{"id": "u1"}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com/", "SECRET-TOKEN")
    out = client.cstools_search("acme")

    assert out == {"items": [{"id": "u1"}]}
    assert captured["url"] == "https://bo.example.com/api/cstools/v2/companies"
    assert captured["json"] == {"text": "acme", "page": 1, "pageSize": 50}
    assert captured["headers"]["Cookie"] == "INTTOKEN=SECRET-TOKEN"
    assert captured["headers"]["Content-Type"] == "application/json"
    # Unmasks BO "sensitive" fields (wallet balances) — otherwise returned as 0.0.
    assert captured["headers"]["sensitive-data"] == "true"
    assert captured["timeout"] == 30.0


def test_list_seizures_url_and_body(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(200, {"seizures": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com", "tok")
    client.list_seizures("company-123")
    assert captured["url"] == "https://bo.example.com/api/transactionmonitoring/company/seizures"
    # Paging params are always sent (the endpoint is paginated, default 10).
    assert captured["json"] == {"companyId": "company-123", "page": 1, "pageSize": 100}


def test_list_seizures_fetches_all_pages(monkeypatch):
    # BO reports totalCount 12 but caps the page — list_seizures must follow
    # pages until every seizure is collected (regression: page 2 was dropped).
    pages = {
        1: {"seizures": [{"id": f"s{i}"} for i in range(100)], "totalCount": 112},
        2: {"seizures": [{"id": f"s{i}"} for i in range(100, 112)], "totalCount": 112},
    }
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["page"])
        return _FakeResp(200, pages[json["page"]])

    monkeypatch.setattr(httpx, "post", fake_post)
    out = BOClient("https://bo.example.com", "tok").list_seizures("c1")
    assert seen == [1, 2]  # stopped once 112 collected
    assert len(out["seizures"]) == 112
    assert out["seizures"][-1]["id"] == "s111"


def test_get_seizure_url(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResp(200, {"id": "s1", "status": "Processing"})

    monkeypatch.setattr(httpx, "get", fake_get)
    client = BOClient("https://bo.example.com", "tok")
    out = client.get_seizure("s1")
    assert out["id"] == "s1"
    assert captured["url"] == "https://bo.example.com/api/transactionmonitoring/seizure/s1"
    assert captured["headers"]["Cookie"] == "INTTOKEN=tok"


def test_wallets_url_cookie_and_flags(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResp(200, {"items": [{"iban": "DE1", "balance": 100.0, "currency": "EUR"}],
                               "totalCount": 1})

    monkeypatch.setattr(httpx, "get", fake_get)
    client = BOClient("https://bo.example.com", "tok")
    out = client.wallets("company-123")

    assert out["items"][0]["balance"] == 100.0
    assert captured["url"] == (
        "https://bo.example.com/api/bank/wallets/?page=1&companyId=company-123"
        "&actualBalanceExcludingDebt=true&actualBalanceExcludingOnHold=true"
    )
    assert captured["headers"]["Cookie"] == "INTTOKEN=tok"


# --- error paths ------------------------------------------------------------


def test_non_2xx_raises_boerror_with_body(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(403, None, text="forbidden details here")

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com", "tok")
    with pytest.raises(BOError) as ei:
        client.cstools_search("x")
    exc = ei.value
    assert exc.status_code == 403
    assert "forbidden details here" in str(exc)
    assert exc.code == 502


def test_body_truncated_to_2000(monkeypatch):
    big = "z" * 5000

    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(500, None, text=big)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com", "tok")
    with pytest.raises(BOError) as ei:
        client.cstools_search("x")
    assert len(ei.value.body) == 2000


def test_transport_error_raises_boerror(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com", "tok")
    with pytest.raises(BOError) as ei:
        client.cstools_search("x")
    assert ei.value.status_code is None
    assert "ConnectError" in str(ei.value)


def test_empty_base_url_raises_without_http(monkeypatch):
    # Should fail fast before any network call.
    def fail(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("httpx should not be called")

    monkeypatch.setattr(httpx, "post", fail)
    monkeypatch.setattr(httpx, "get", fail)
    client = BOClient("", "tok")
    with pytest.raises(BOError) as ei:
        client.cstools_search("x")
    assert "not configured" in str(ei.value)


def test_token_never_in_error_message(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(401, None, text="unauthorized")

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BOClient("https://bo.example.com", "TOP-SECRET-TOKEN")
    with pytest.raises(BOError) as ei:
        client.cstools_search("x")
    assert "TOP-SECRET-TOKEN" not in str(ei.value)


# --- new read-only endpoints (11-scenario support) ----------------------------


def test_get_alerts_url_and_body(monkeypatch):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"], calls["json"], calls["headers"] = url, json, headers
        return _FakeResp(200, {"items": [], "totalCount": 0})

    monkeypatch.setattr(bo_client.httpx, "post", fake_post)
    BOClient("https://bo.example", "tok").get_alerts("u-1")
    assert calls["url"] == "https://bo.example/api/transactionmonitoring/companies/u-1/alerts"
    assert calls["json"] == {"filters": {}}
    assert calls["headers"]["Cookie"] == "INTTOKEN=tok"


def test_short_info_overview_cdd_urls(monkeypatch):
    urls = []

    def fake_get(url, headers=None, timeout=None):
        urls.append(url)
        return _FakeResp(200, {})

    monkeypatch.setattr(bo_client.httpx, "get", fake_get)
    c = BOClient("https://bo.example", "tok")
    c.cstools_short_info("u-1")
    c.cstools_overview("u-1")
    c.cdd_profile("u-1")
    assert urls == [
        "https://bo.example/api/cstools/companies/u-1/short-info",
        "https://bo.example/api/cstools/companies/u-1/overview",
        "https://bo.example/api/customerdossier/companies/u-1/cdd-profile",
    ]


def test_client_has_no_write_methods():
    # bigi is read-only toward BO: creating/executing seizures must be impossible.
    for name in ("create_seizure", "tm_create_seizure", "execute", "post_seizure"):
        assert not hasattr(BOClient("", ""), name)


def test_whoami_and_user_context_urls(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(("GET", url, None))
        return _FakeResp(200, {"contexts": ["FinomPayments"], "activeContexts": ["FinomPayments"]})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(("POST", url, json))
        return _FakeResp(200, {})

    monkeypatch.setattr(bo_client.httpx, "get", fake_get)
    monkeypatch.setattr(bo_client.httpx, "post", fake_post)
    c = BOClient("https://bo.example", "tok")
    c.whoami()
    c.set_user_contexts(["FinomPayments", "PnlFintech"])
    assert calls[0][:2] == ("GET", "https://bo.example/api/cstools/whoami")
    assert calls[1][:2] == ("POST", "https://bo.example/api/cstools/user-context/set")
    assert calls[1][2] == {"userContexts": ["FinomPayments", "PnlFintech"]}
