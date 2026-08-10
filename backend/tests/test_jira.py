"""Jira helper tests (offline): ADF flatten + fetch/search with httpx patched.

We assert the Basic-auth header is present and that the API token never leaks
into the call as anything other than that header value.
"""
from __future__ import annotations

import base64

import pytest

from app import jira
from app.schemas import BigiError


CFG = {
    "base_url": "https://acme.atlassian.net",
    "email": "ops@acme.io",
    "api_token": "secret-token-1234",
    "jql": "project = SEIZ ORDER BY created DESC",
}


def _expected_auth():
    raw = f"{CFG['email']}:{CFG['api_token']}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


# --- flatten_adf ------------------------------------------------------------
def test_flatten_adf_nested():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Line one"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "Line two"},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "item a"}]}]},
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "item b"}]}]},
                ],
            },
        ],
    }
    out = jira.flatten_adf(doc)
    assert "Title" in out
    assert "Line one" in out
    assert "Line two" in out
    assert "item a" in out
    assert "item b" in out
    # hardBreak introduces a newline between the two lines.
    assert "Line one\nLine two" in out


def test_flatten_adf_string_passthrough():
    assert jira.flatten_adf("plain") == "plain"


def test_flatten_adf_none_and_garbage():
    assert jira.flatten_adf(None) == ""
    assert jira.flatten_adf(123) == "123"


# --- fetch_issue ------------------------------------------------------------
def test_fetch_issue_sends_basic_auth(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        return _Resp(payload={
            "key": "SEIZ-1",
            "fields": {
                "summary": "A seizure",
                "description": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "body text"}]}
                    ],
                },
            },
        })

    monkeypatch.setattr(jira.httpx, "get", fake_get)
    out = jira.fetch_issue(CFG, "SEIZ-1")

    assert out == {"key": "SEIZ-1", "summary": "A seizure", "description": "body text"}
    assert seen["url"] == "https://acme.atlassian.net/rest/api/3/issue/SEIZ-1"
    assert seen["headers"]["Authorization"] == _expected_auth()
    # The raw token only ever appears inside the encoded Basic header.
    assert seen["headers"]["Authorization"] != CFG["api_token"]
    for v in (seen["url"], str(seen["params"])):
        assert CFG["api_token"] not in v


@pytest.mark.parametrize(
    ("ref", "key"),
    [
        ("SEIZ-1234", "SEIZ-1234"),
        ("  seiz-1234  ", "SEIZ-1234"),
        ("https://acme.atlassian.net/browse/SEIZ-1234", "SEIZ-1234"),
        ("https://acme.atlassian.net/browse/SEIZ-1234?focusedCommentId=9", "SEIZ-1234"),
        ("https://acme.atlassian.net/browse/seiz-1234#comment-9", "SEIZ-1234"),
        (
            "https://acme.atlassian.net/jira/software/c/projects/SEIZ/boards/1?selectedIssue=SEIZ-77",
            "SEIZ-77",
        ),
        (
            "https://acme.atlassian.net/jira/servicedesk/projects/SEIZ/queues/custom/3/SEIZ-42",
            "SEIZ-42",
        ),
        ("https://acme.atlassian.net/servicedesk/customer/portal/1/SEIZ-8", "SEIZ-8"),
        # Hostname must never be mistaken for a key.
        ("https://foo-1.atlassian.net/browse/SEIZ-5", "SEIZ-5"),
        # Unrecognized input passes through untouched (fails later as not-found).
        ("not a key", "not a key"),
        ("https://acme.atlassian.net/jira/dashboards", "https://acme.atlassian.net/jira/dashboards"),
    ],
)
def test_normalize_issue_ref(ref, key):
    assert jira.normalize_issue_ref(ref) == key


def test_fetch_issue_accepts_browse_link(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        return _Resp(payload={"key": "SEIZ-1", "fields": {"summary": "s", "description": None}})

    monkeypatch.setattr(jira.httpx, "get", fake_get)
    out = jira.fetch_issue(CFG, "https://acme.atlassian.net/browse/SEIZ-1?focusedCommentId=2")

    assert out["key"] == "SEIZ-1"
    assert seen["url"] == "https://acme.atlassian.net/rest/api/3/issue/SEIZ-1"


def test_fetch_issue_404(monkeypatch):
    monkeypatch.setattr(jira.httpx, "get", lambda *a, **k: _Resp(status_code=404, text="nope"))
    with pytest.raises(BigiError):
        jira.fetch_issue(CFG, "SEIZ-404")


def test_fetch_issue_unconfigured():
    with pytest.raises(BigiError):
        jira.fetch_issue({"base_url": "", "email": "", "api_token": ""}, "SEIZ-1")


# --- /api/jira/fetch route ---------------------------------------------------
def test_fetch_route_returns_description_for_repick(monkeypatch, client):
    """The UI keeps jira.description as the result's source text so candidate
    selection / manual UUID entry can re-run the pipeline without re-pasting."""
    client.put("/api/settings", json={"jira": {
        "base_url": CFG["base_url"], "email": CFG["email"], "api_token": CFG["api_token"],
    }})

    def fake_get(url, headers=None, params=None, timeout=None):
        return _Resp(payload={"key": "SEIZ-9", "fields": {
            "summary": "Seizure - Muster GmbH",
            "description": "seizure amount: 100,00\ndebtor name: Muster GmbH\n",
        }})

    monkeypatch.setattr(jira.httpx, "get", fake_get)
    res = client.post("/api/jira/fetch", json={"issue_key": "SEIZ-9"})

    assert res.status_code == 200
    body = res.json()
    assert body["jira"] == {
        "key": "SEIZ-9",
        "summary": "Seizure - Muster GmbH",
        "description": "seizure amount: 100,00\ndebtor name: Muster GmbH",
    }
    assert body["parsed"]["debtor_name"] == "Muster GmbH"


# --- search_issues ----------------------------------------------------------
def test_search_issues_uses_default_jql(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        return _Resp(payload={"issues": [
            {"key": "SEIZ-1", "fields": {"summary": "one"}},
            {"key": "SEIZ-2", "fields": {"summary": "two"}},
        ]})

    monkeypatch.setattr(jira.httpx, "get", fake_get)
    out = jira.search_issues(CFG, None)

    assert out == [{"key": "SEIZ-1", "summary": "one"}, {"key": "SEIZ-2", "summary": "two"}]
    # Hits the enhanced endpoint, NOT the removed /rest/api/3/search (HTTP 410).
    assert seen["url"] == "https://acme.atlassian.net/rest/api/3/search/jql"
    # Falls back to the configured jql when none passed.
    assert seen["params"]["jql"] == CFG["jql"]
    assert seen["headers"]["Authorization"] == _expected_auth()


def test_search_issues_explicit_jql(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        jira.httpx, "get",
        lambda url, headers=None, params=None, timeout=None: (
            seen.update(params=params) or _Resp(payload={"issues": []})
        ),
    )
    jira.search_issues(CFG, "status = Open")
    assert seen["params"]["jql"] == "status = Open"


def test_search_issues_bounds_unbounded_jql(monkeypatch):
    """A bare ORDER BY (legacy default) is rewritten to a bounded query so the
    enhanced /search/jql endpoint doesn't reject it with HTTP 400."""
    seen = {}
    monkeypatch.setattr(
        jira.httpx, "get",
        lambda url, headers=None, params=None, timeout=None: (
            seen.update(params=params) or _Resp(payload={"issues": []})
        ),
    )
    jira.search_issues({**CFG, "jql": "ORDER BY created DESC"}, None)
    sent = seen["params"]["jql"]
    assert not sent.lower().startswith("order by")
    assert "created >=" in sent
    # An already-restricted query is left untouched.
    assert jira._ensure_bounded("project = SEIZ ORDER BY created DESC") == (
        "project = SEIZ ORDER BY created DESC"
    )


# --- match UUIDs from comments (submitters post them there now) --------------------


def test_extract_match_uuids_labeled_and_bare():
    from app.jira import extract_match_uuids

    text = ("Hello, definitive match: 11111111-1111-1111-1111-111111111111\n"
            "also potential match 22222222-2222-2222-2222-222222222222 maybe\n"
            "unrelated: 33333333-3333-3333-3333-333333333333")
    out = extract_match_uuids(text)
    assert out == ["11111111-1111-1111-1111-111111111111",
                   "22222222-2222-2222-2222-222222222222",
                   "33333333-3333-3333-3333-333333333333"]


def test_extract_match_uuids_dedupes_and_handles_empty():
    from app.jira import extract_match_uuids

    assert extract_match_uuids("definitive match: 11111111-1111-1111-1111-111111111111 "
                               "and again 11111111-1111-1111-1111-111111111111") == [
        "11111111-1111-1111-1111-111111111111"]
    assert extract_match_uuids("no uuids here") == []
    assert extract_match_uuids("") == []


def test_fetch_comment_match_uuids(monkeypatch):
    from app import jira as jira_mod

    body_adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "definitive match: 11111111-1111-1111-1111-111111111111"}]}]}

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url.endswith("/rest/api/3/issue/FPOPCL-1/comment")
        return _Resp(200, {"comments": [{"body": body_adf},
                                        {"body": "plain 22222222-2222-2222-2222-222222222222"}]})

    monkeypatch.setattr(jira_mod.httpx, "get", fake_get)
    cfg = {"base_url": "https://x.atlassian.net", "email": "e@x", "api_token": "t"}
    out = jira_mod.fetch_comment_match_uuids(cfg, "FPOPCL-1")
    assert out == ["11111111-1111-1111-1111-111111111111",
                   "22222222-2222-2222-2222-222222222222"]


def test_fetch_comment_match_uuids_failure_is_empty(monkeypatch):
    from app import jira as jira_mod

    def fake_get(url, headers=None, params=None, timeout=None):
        return _Resp(500, {})

    monkeypatch.setattr(jira_mod.httpx, "get", fake_get)
    cfg = {"base_url": "https://x.atlassian.net", "email": "e@x", "api_token": "t"}
    assert jira_mod.fetch_comment_match_uuids(cfg, "FPOPCL-1") == []
