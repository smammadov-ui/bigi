import React, { useEffect, useState } from 'react';
import {
  getSettings,
  putSettings,
  testBO,
  testJira,
  testLLM,
} from './api.js';

// Placeholder for a secret input: shows the masked value + "set" when one is
// stored, otherwise a neutral hint. The actual secret is never sent to the
// browser, so an empty field always means "leave unchanged".
function secretPlaceholder(masked, isSet, source) {
  if (!isSet) return 'not set';
  if (source === 'env') return `${masked} — from environment (.env); saving here overrides`;
  return `${masked} — set (leave blank to keep)`;
}

export default function Settings() {
  const [view, setView] = useState(null); // public_view from the backend
  const [loadErr, setLoadErr] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // Editable form state. Non-secret fields are seeded from the view; secret
  // fields start blank (blank = unchanged on save).
  const [llm, setLLM] = useState({ provider: 'openai', model: '', api_key: '' });
  const [bo, setBO] = useState({ base_url: '', inttoken: '' });
  const [jira, setJira] = useState({ base_url: '', email: '', api_token: '', jql: '' });

  // Per-secret "clear" toggles — when on, we explicitly send "" to clear it.
  const [clear, setClear] = useState({ llm: false, bo: false, jira: false });

  // Test-connection results: {bo, jira, llm} -> {ok, detail} | {busy:true}
  const [tests, setTests] = useState({});

  function seed(v) {
    setView(v);
    setLLM({ provider: v.llm.provider || 'openai', model: v.llm.model || '', api_key: '' });
    setBO({ base_url: v.bo.base_url || '', inttoken: '' });
    setJira({
      base_url: v.jira.base_url || '',
      email: v.jira.email || '',
      api_token: '',
      jql: v.jira.jql || '',
    });
    setClear({ llm: false, bo: false, jira: false });
  }

  useEffect(() => {
    getSettings()
      .then(seed)
      .catch((e) => setLoadErr(e.message));
  }, []);

  // Build a minimal patch: only include fields the operator actually changed.
  // - Non-secret fields: include when different from the loaded view.
  // - Secret fields: include "" when the clear toggle is on; include the typed
  //   value when non-empty; otherwise omit (= unchanged).
  function buildPatch() {
    if (!view) return {};
    const patch = {};

    // LLM
    const llmPatch = {};
    if (llm.provider !== (view.llm.provider || 'openai')) llmPatch.provider = llm.provider;
    if (llm.model !== (view.llm.model || '')) llmPatch.model = llm.model;
    if (clear.llm) llmPatch.api_key = '';
    else if (llm.api_key) llmPatch.api_key = llm.api_key;
    if (Object.keys(llmPatch).length) patch.llm = llmPatch;

    // BO
    const boPatch = {};
    if (bo.base_url !== (view.bo.base_url || '')) boPatch.base_url = bo.base_url;
    if (clear.bo) boPatch.inttoken = '';
    else if (bo.inttoken) boPatch.inttoken = bo.inttoken;
    if (Object.keys(boPatch).length) patch.bo = boPatch;

    // Jira
    const jiraPatch = {};
    if (jira.base_url !== (view.jira.base_url || '')) jiraPatch.base_url = jira.base_url;
    if (jira.email !== (view.jira.email || '')) jiraPatch.email = jira.email;
    if (jira.jql !== (view.jira.jql || '')) jiraPatch.jql = jira.jql;
    if (clear.jira) jiraPatch.api_token = '';
    else if (jira.api_token) jiraPatch.api_token = jira.api_token;
    if (Object.keys(jiraPatch).length) patch.jira = jiraPatch;

    return patch;
  }

  async function save() {
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) {
      setSaveMsg('Nothing changed.');
      return;
    }
    setSaving(true);
    setSaveMsg('');
    try {
      const v = await putSettings(patch);
      seed(v); // re-seed: secrets blank again, masked placeholders refreshed
      setSaveMsg('Saved.');
    } catch (e) {
      setSaveMsg(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function runTest(name, fn) {
    setTests((t) => ({ ...t, [name]: { busy: true } }));
    try {
      const r = await fn();
      setTests((t) => ({ ...t, [name]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [name]: { ok: false, detail: e.message } }));
    }
  }

  function TestRow({ name, fn }) {
    const r = tests[name];
    return (
      <div>
        <button className="btn small" onClick={() => runTest(name, fn)}>
          Test connection
        </button>
        {r && (
          <span className="test-result">
            {' '}
            {r.busy ? (
              <span className="muted">
                <span className="spinner" /> testing…
              </span>
            ) : (
              <span className={r.ok ? 'ok' : 'bad'}>
                {r.ok ? '✓ ' : '✗ '}
                {r.detail}
              </span>
            )}
          </span>
        )}
      </div>
    );
  }

  if (loadErr) return <div className="banner err">Could not load settings: {loadErr}</div>;
  if (!view) {
    return (
      <div className="muted">
        <span className="spinner" /> Loading settings…
      </div>
    );
  }

  return (
    <>
      {/* LLM */}
      <div className="card">
        <h2>LLM</h2>
        <div className="row">
          <div className="field">
            <label htmlFor="llm-provider">Provider</label>
            <select
              id="llm-provider"
              value={llm.provider}
              onChange={(e) => setLLM({ ...llm, provider: e.target.value })}
            >
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="llm-model">Model</label>
            <input
              id="llm-model"
              type="text"
              placeholder={llm.provider === 'anthropic' ? 'claude-sonnet-5' : 'gpt-4o-mini'}
              value={llm.model}
              onChange={(e) => setLLM({ ...llm, model: e.target.value })}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="llm-key">API key</label>
          <input
            id="llm-key"
            type="password"
            placeholder={secretPlaceholder(view.llm.api_key_masked, view.llm.api_key_set, view.llm.api_key_source)}
            value={llm.api_key}
            disabled={clear.llm}
            onChange={(e) => setLLM({ ...llm, api_key: e.target.value })}
          />
          {view.llm.api_key_set && (
            <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
              <input
                type="checkbox"
                style={{ width: 'auto' }}
                checked={clear.llm}
                onChange={(e) => setClear({ ...clear, llm: e.target.checked })}
              />
              Clear stored key
            </label>
          )}
        </div>
        <TestRow name="llm" fn={testLLM} />
      </div>

      {/* Back-Office */}
      <div className="card">
        <h2>Back-Office (Finom)</h2>
        <div className="field">
          <label htmlFor="bo-url">Base URL</label>
          <input
            id="bo-url"
            type="text"
            placeholder="https://…"
            value={bo.base_url}
            onChange={(e) => setBO({ ...bo, base_url: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="bo-token">INTTOKEN</label>
          <input
            id="bo-token"
            type="password"
            placeholder={secretPlaceholder(view.bo.inttoken_masked, view.bo.inttoken_set, view.bo.inttoken_source)}
            value={bo.inttoken}
            disabled={clear.bo}
            onChange={(e) => setBO({ ...bo, inttoken: e.target.value })}
          />
          {view.bo.inttoken_set && (
            <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
              <input
                type="checkbox"
                style={{ width: 'auto' }}
                checked={clear.bo}
                onChange={(e) => setClear({ ...clear, bo: e.target.checked })}
              />
              Clear stored token
            </label>
          )}
        </div>
        <TestRow name="bo" fn={testBO} />
      </div>

      {/* Jira */}
      <div className="card">
        <h2>Jira</h2>
        <div className="row">
          <div className="field">
            <label htmlFor="jira-url">Base URL</label>
            <input
              id="jira-url"
              type="text"
              placeholder="https://your-domain.atlassian.net"
              value={jira.base_url}
              onChange={(e) => setJira({ ...jira, base_url: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="jira-email">Email</label>
            <input
              id="jira-email"
              type="email"
              placeholder="you@company.com"
              value={jira.email}
              onChange={(e) => setJira({ ...jira, email: e.target.value })}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="jira-token">API token</label>
          <input
            id="jira-token"
            type="password"
            placeholder={secretPlaceholder(view.jira.api_token_masked, view.jira.api_token_set, view.jira.api_token_source)}
            value={jira.api_token}
            disabled={clear.jira}
            onChange={(e) => setJira({ ...jira, api_token: e.target.value })}
          />
          {view.jira.api_token_set && (
            <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
              <input
                type="checkbox"
                style={{ width: 'auto' }}
                checked={clear.jira}
                onChange={(e) => setClear({ ...clear, jira: e.target.checked })}
              />
              Clear stored token
            </label>
          )}
        </div>
        <div className="field">
          <label htmlFor="jira-jql">Default JQL (Browse)</label>
          <input
            id="jira-jql"
            type="text"
            placeholder="project = FPOPCL ORDER BY created DESC"
            value={jira.jql}
            onChange={(e) => setJira({ ...jira, jql: e.target.value })}
          />
          <div className="hint">
            Jira rejects unbounded queries — include a restriction (a project or
            date filter). A bare “ORDER BY …” is auto-limited to the last 90 days.
          </div>
        </div>
        <TestRow name="jira" fn={testJira} />
      </div>

      <div className="inline">
        <button className="btn primary" disabled={saving} onClick={save}>
          {saving ? <span className="spinner" /> : null} Save changes
        </button>
        {saveMsg && <span className="muted small">{saveMsg}</span>}
      </div>
    </>
  );
}
