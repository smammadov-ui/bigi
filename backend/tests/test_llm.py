"""Tests for app.llm.compose — deterministic fallback, provider routing, guards.

All network calls are stubbed by monkeypatching httpx.post (and the request via the
SDK-free raw-httpx path). No real HTTP is ever made.
"""
from app import llm
from app.templates import TEMPLATES, deterministic_fill

_FIELDS = {
    "case_references": "12 M 3456/26",
    "creditor_name": "Finanzamt Berlin",
    "creditor_address": "Musterstr. 1, 10115 Berlin",
    "debtor_name": "Max Mustermann GmbH",
    "date_received": "2026-02-03",
    "seizure_amount": "1234.50",
}

# A valid composed declaration the LLM might return (contains both anchors).
_GOOD_OUTPUT = deterministic_fill("T1", _FIELDS, comments_de=[])


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _openai_body(content):
    return {"choices": [{"message": {"content": content}}]}


def _anthropic_body(content, stop_reason="end_turn"):
    return {
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": content}],
    }


def test_no_api_key_uses_deterministic():
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[], llm_cfg={"provider": "openai", "api_key": ""}
    )
    assert composed_by == "deterministic"
    assert text == deterministic_fill("T1", _FIELDS, comments_de=[])


def test_openai_success(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return _FakeResponse(_openai_body(_GOOD_OUTPUT))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "model": "", "api_key": "sk-test"},
    )
    assert composed_by == "llm:openai"
    assert text == _GOOD_OUTPUT
    assert calls["url"] == "https://api.openai.com/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer sk-test"
    assert calls["json"]["temperature"] == 0
    assert calls["json"]["model"] == "gpt-4o-mini"  # default applied


def test_anthropic_success(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return _FakeResponse(_anthropic_body(_GOOD_OUTPUT))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "anthropic", "model": "", "api_key": "sk-ant"},
    )
    assert composed_by == "llm:anthropic"
    assert text == _GOOD_OUTPUT
    assert calls["url"] == "https://api.anthropic.com/v1/messages"
    assert calls["headers"]["x-api-key"] == "sk-ant"
    assert calls["headers"]["anthropic-version"] == "2023-06-01"
    assert calls["json"]["model"] == "claude-sonnet-5"  # default applied
    # No temperature/top_p/thinking sent (keeps the request maximally compatible).
    assert "temperature" not in calls["json"]
    assert "top_p" not in calls["json"]
    assert "thinking" not in calls["json"]


def test_anthropic_refusal_falls_back(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(_anthropic_body("I cannot help with that.", stop_reason="refusal"))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "anthropic", "api_key": "sk-ant"},
    )
    assert composed_by == "deterministic"
    assert text == deterministic_fill("T1", _FIELDS, comments_de=[])


def test_garbage_output_missing_anchors_falls_back(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(_openai_body("Sorry, here is something unrelated."))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "deterministic"
    assert text == deterministic_fill("T1", _FIELDS, comments_de=[])


def test_empty_output_falls_back(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(_openai_body("   "))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "deterministic"


def test_transport_error_falls_back(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise llm.httpx.ConnectError("boom")

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "deterministic"
    assert text == deterministic_fill("T1", _FIELDS, comments_de=[])


def test_code_fence_is_stripped(monkeypatch):
    fenced = "```\n" + _GOOD_OUTPUT + "\n```"

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(_openai_body(fenced))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "llm:openai"
    assert text == _GOOD_OUTPUT
    assert "```" not in text


def test_anthropic_joins_text_blocks(monkeypatch):
    # Multiple text blocks plus a non-text block -> only text blocks joined.
    half_a = _GOOD_OUTPUT[: len(_GOOD_OUTPUT) // 2]
    half_b = _GOOD_OUTPUT[len(_GOOD_OUTPUT) // 2:]

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse({
            "stop_reason": "end_turn",
            "content": [
                {"type": "text", "text": half_a},
                {"type": "tool_use", "id": "x"},
                {"type": "text", "text": half_b},
            ],
        })

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "anthropic", "api_key": "sk-ant"},
    )
    assert composed_by == "llm:anthropic"
    assert text == _GOOD_OUTPUT


def test_unknown_provider_falls_back(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):  # pragma: no cover
        raise AssertionError("should not be called for unknown provider")

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "gemini", "api_key": "sk-x"},
    )
    assert composed_by == "deterministic"


def test_t2_comments_passed_in_user_message(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(_openai_body(deterministic_fill("T2", _FIELDS, ["x"])))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.compose(
        "T2", TEMPLATES["T2"], _FIELDS, comments=["Pfändung läuft noch."],
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    user_msg = captured["json"]["messages"][1]["content"]
    # The pre-rendered bullet block is passed as the [Comment] field value
    # (JSON-encoded, so the tab shows as \t); the template body is verbatim.
    assert "\\t• Pfändung läuft noch." in user_msg
    assert "[Comment]" in user_msg
    assert "§ 840" in user_msg


def test_t2_llm_output_merging_comments_falls_back(monkeypatch):
    # Two ongoing seizures, but the LLM merged them into ONE bullet -> the
    # merged output is rejected and the deterministic fill (both comments,
    # untranslated) is used instead.
    merged = deterministic_fill("T2", _FIELDS, ["seizure A merged with seizure B"])
    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(_openai_body(merged))
    )
    comments = ["seizure A, amount 13470.45", "seizure B, amount 1513.23"]
    text, composed_by = llm.compose(
        "T2", TEMPLATES["T2"], _FIELDS, comments=comments,
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "deterministic"
    assert "\t• seizure A, amount 13470.45\n\t• seizure B, amount 1513.23" in text


def test_t2_llm_output_with_one_bullet_per_comment_is_accepted(monkeypatch):
    comments = ["seizure A, amount 13470.45", "seizure B, amount 1513.23"]
    good = deterministic_fill("T2", _FIELDS, ["Pfändung A, Betrag 13.470,45 EUR",
                                              "Pfändung B, Betrag 1.513,23 EUR"])
    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(_openai_body(good))
    )
    text, composed_by = llm.compose(
        "T2", TEMPLATES["T2"], _FIELDS, comments=comments,
        llm_cfg={"provider": "openai", "api_key": "sk-test"},
    )
    assert composed_by == "llm:openai"
    assert text.count("\t• ") == 2


def test_model_override_is_used(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(_anthropic_body(_GOOD_OUTPUT))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "anthropic", "model": "claude-opus-4-7", "api_key": "sk-ant"},
    )
    assert captured["json"]["model"] == "claude-opus-4-7"


def test_compose_threads_seized_eur_into_deterministic():
    # No api key -> deterministic; seized_eur fills [Seized amount].
    text, composed_by = llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": ""}, seized_eur=500.0,
    )
    assert composed_by == "deterministic"
    assert "in Höhe von 500,00 EUR" in text
    assert text == deterministic_fill("T1", _FIELDS, comments_de=[], seized_eur=500.0)


def test_compose_user_message_includes_seized_eur(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(_openai_body(_GOOD_OUTPUT))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.compose(
        "T1", TEMPLATES["T1"], _FIELDS, comments=[],
        llm_cfg={"provider": "openai", "api_key": "sk-test"}, seized_eur=500.0,
    )
    user_msg = captured["json"]["messages"][1]["content"]
    assert "500,00" in user_msg
