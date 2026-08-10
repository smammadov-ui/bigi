"""Settings store + router behaviour (offline)."""
from __future__ import annotations

from app import settings_store


# --- store-level ---------------------------------------------------------- #

def test_defaults_when_unset(db):
    assert settings_store.get(db, "llm_provider") == "openai"
    assert settings_store.get(db, "jira_jql") == "project = FPOPCL ORDER BY created DESC"
    assert settings_store.get(db, "bo_base_url") == ""


def test_mask_helper():
    assert settings_store.mask("abcd1234") == "••••1234"
    assert settings_store.mask("ab") == "••••"
    assert settings_store.mask("") == ""
    assert settings_store.mask("   ") == ""


def test_update_and_public_view_masks_secrets(db):
    settings_store.update(
        db,
        {
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "llm_api_key": "sk-secret-1234",
            "bo_base_url": "https://bo.example",
            "bo_inttoken": "tokABCDab12",
            "jira_base_url": "https://jira.example",
            "jira_email": "a@b.c",
            "jira_api_token": "jiraXXXX12",
        },
    )
    view = settings_store.public_view(db)
    assert view["llm"]["provider"] == "openai"
    assert view["llm"]["model"] == "gpt-4o-mini"
    assert view["llm"]["api_key_masked"] == "••••1234"
    assert view["llm"]["api_key_set"] is True
    assert view["bo"]["base_url"] == "https://bo.example"
    assert view["bo"]["inttoken_masked"] == "••••ab12"
    assert view["bo"]["inttoken_set"] is True
    assert view["jira"]["email"] == "a@b.c"
    assert view["jira"]["api_token_masked"] == "••••XX12"
    assert view["jira"]["api_token_set"] is True
    # Plaintext secrets must never appear in the public view.
    blob = str(view)
    assert "sk-secret-1234" not in blob
    assert "tokABCDab12" not in blob
    assert "jiraXXXX12" not in blob


def test_empty_string_clears_secret_but_omit_leaves_unchanged(db):
    settings_store.update(db, {"bo_inttoken": "tokABCDab12"})
    assert settings_store.public_view(db)["bo"]["inttoken_set"] is True
    # Omitting the key leaves it unchanged.
    settings_store.update(db, {"bo_base_url": "https://bo.example"})
    assert settings_store.public_view(db)["bo"]["inttoken_set"] is True
    # Explicit empty string clears it.
    settings_store.update(db, {"bo_inttoken": ""})
    assert settings_store.public_view(db)["bo"]["inttoken_set"] is False


def test_invalid_provider_rejected(db):
    from app.schemas import BigiError

    try:
        settings_store.update(db, {"llm_provider": "bogus"})
        assert False, "expected BigiError"
    except BigiError:
        pass


def test_typed_getters_return_real_secrets(db):
    settings_store.update(db, {"llm_api_key": "sk-real", "bo_inttoken": "int-real"})
    assert settings_store.llm_config(db)["api_key"] == "sk-real"
    assert settings_store.bo_config(db)["inttoken"] == "int-real"


# --- router-level --------------------------------------------------------- #

def test_get_settings_endpoint(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["provider"] == "openai"
    assert body["jira"]["jql"] == "project = FPOPCL ORDER BY created DESC"


def test_put_then_get_masks(client):
    r = client.put(
        "/api/settings",
        json={
            "llm": {"provider": "anthropic", "model": "claude-opus-4-8", "api_key": "sk-abcd1234"},
            "bo": {"base_url": "https://bo.example", "inttoken": "tokWXYZ9999"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["provider"] == "anthropic"
    assert body["llm"]["api_key_masked"] == "••••1234"
    assert body["llm"]["api_key_set"] is True
    assert "sk-abcd1234" not in str(body)
    # GET reflects persisted state.
    g = client.get("/api/settings").json()
    assert g["bo"]["inttoken_masked"] == "••••9999"
    assert g["bo"]["inttoken_set"] is True


def test_put_omitted_secret_left_unchanged(client):
    client.put("/api/settings", json={"llm": {"api_key": "sk-keepme01"}})
    # Update only the model; do NOT send api_key.
    r = client.put("/api/settings", json={"llm": {"model": "gpt-4o"}})
    body = r.json()
    assert body["llm"]["model"] == "gpt-4o"
    assert body["llm"]["api_key_set"] is True
    assert body["llm"]["api_key_masked"] == "••••me01"


def test_put_empty_string_clears_secret(client):
    client.put("/api/settings", json={"jira": {"api_token": "tokenABCD12"}})
    assert client.get("/api/settings").json()["jira"]["api_token_set"] is True
    r = client.put("/api/settings", json={"jira": {"api_token": ""}})
    assert r.json()["jira"]["api_token_set"] is False


def test_put_invalid_provider_400(client):
    r = client.put("/api/settings", json={"llm": {"provider": "nope"}})
    assert r.status_code == 400
    assert "provider" in r.json()["detail"].lower()


def test_test_bo_unconfigured(client):
    r = client.post("/api/settings/test/bo")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not set" in body["detail"].lower()


def test_test_llm_unconfigured(client):
    r = client.post("/api/settings/test/llm")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_test_jira_unconfigured(client):
    r = client.post("/api/settings/test/jira")
    assert r.status_code == 200
    assert r.json()["ok"] is False


# --- environment fallbacks ------------------------------------------------------


def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()


def test_env_fallback_used_when_db_unset(db, monkeypatch):
    monkeypatch.setenv("BO_BASE_URL", "https://bo.env.example")
    monkeypatch.setenv("BO_INTTOKEN", "env-token-1234")
    _clear_settings_cache()
    try:
        cfg = settings_store.bo_config(db)
        assert cfg["base_url"] == "https://bo.env.example"
        assert cfg["inttoken"] == "env-token-1234"
        view = settings_store.public_view(db)
        assert view["bo"]["inttoken_set"] is True
        assert view["bo"]["inttoken_source"] == "env"
        assert view["bo"]["inttoken_masked"] == "••••1234"   # masked, never plaintext
        assert "env-token-1234" not in str(view)
    finally:
        _clear_settings_cache()


def test_db_value_overrides_env(db, monkeypatch):
    monkeypatch.setenv("BO_INTTOKEN", "env-token-1234")
    _clear_settings_cache()
    try:
        settings_store.update(db, {"bo_inttoken": "ui-token-9999"})
        cfg = settings_store.bo_config(db)
        assert cfg["inttoken"] == "ui-token-9999"
        assert settings_store.public_view(db)["bo"]["inttoken_source"] == "db"
    finally:
        _clear_settings_cache()


def test_no_env_no_db_is_default(db):
    view = settings_store.public_view(db)
    assert view["bo"]["inttoken_set"] is False
    assert view["bo"]["inttoken_source"] == "default"


def test_llm_env_fallback(db, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-env-5678")
    _clear_settings_cache()
    try:
        cfg = settings_store.llm_config(db)
        assert cfg["provider"] == "anthropic"
        assert cfg["api_key"] == "sk-ant-env-5678"
    finally:
        _clear_settings_cache()


def test_bo_token_file_fallback(db, monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token-9999\n")
    monkeypatch.setenv("BO_INTTOKEN", "")                 # env unset
    monkeypatch.setenv("BO_INTTOKEN_FILE", str(token_file))
    _clear_settings_cache()
    try:
        assert settings_store.bo_config(db)["inttoken"] == "file-token-9999"
        # Refreshing the FILE takes effect without any cache reset.
        token_file.write_text("file-token-0000")
        assert settings_store.bo_config(db)["inttoken"] == "file-token-0000"
        view = settings_store.public_view(db)
        assert view["bo"]["inttoken_masked"] == "••••0000"
    finally:
        _clear_settings_cache()


def test_env_token_beats_file(db, monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token-9999")
    monkeypatch.setenv("BO_INTTOKEN", "env-token-1234")
    monkeypatch.setenv("BO_INTTOKEN_FILE", str(token_file))
    _clear_settings_cache()
    try:
        assert settings_store.bo_config(db)["inttoken"] == "env-token-1234"
    finally:
        _clear_settings_cache()


def test_missing_token_file_is_harmless(db, monkeypatch):
    monkeypatch.setenv("BO_INTTOKEN", "")
    monkeypatch.setenv("BO_INTTOKEN_FILE", "/nonexistent/path/token")
    _clear_settings_cache()
    try:
        assert settings_store.bo_config(db)["inttoken"] == ""
    finally:
        _clear_settings_cache()
