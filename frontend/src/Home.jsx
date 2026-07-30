import React, { useState } from 'react';
import { postDeclaration, jiraFetch, jiraSearch } from './api.js';
import FieldTable from './components/FieldTable.jsx';
import AccountPanel from './components/AccountPanel.jsx';
import AlertsPanel from './components/AlertsPanel.jsx';
import BalancePanel from './components/BalancePanel.jsx';
import ScenarioPanel from './components/ScenarioPanel.jsx';
import SeizureCheck from './components/SeizureCheck.jsx';
import DeclarationEditor from './components/DeclarationEditor.jsx';
import Toast from './components/Toast.jsx';

export default function Home() {
  const [raw, setRaw] = useState('');
  const [issueKey, setIssueKey] = useState('');

  const [result, setResult] = useState(null); // pipeline result dict
  const [jiraMeta, setJiraMeta] = useState(null); // {key, summary} when fetched
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');

  // Manual paste box visibility. Operators who work straight from Jira can
  // collapse it to declutter the Input card; the choice persists across cases.
  const [pasteOpen, setPasteOpen] = useState(false);

  // Jira browse
  const [browseOpen, setBrowseOpen] = useState(false);
  const [issues, setIssues] = useState(null);
  const [browseBusy, setBrowseBusy] = useState(false);

  // The raw text the current result was produced from — needed to re-run with a
  // chosen company_uuid after candidate selection / manual entry.
  const [activeRaw, setActiveRaw] = useState('');

  const showResult = (res, meta, srcRaw) => {
    setResult(res);
    setJiraMeta(meta || null);
    setActiveRaw(srcRaw);
  };

  // Clear everything and return Home to a blank slate for the next ticket.
  // NOTE: no window.confirm() — native dialogs don't work in the Tauri webview
  // (it returns false without prompting), which previously made this a no-op.
  function resetCase() {
    setRaw('');
    setIssueKey('');
    setResult(null);
    setJiraMeta(null);
    setError('');
    setToast('');
    setBrowseOpen(false);
    setIssues(null);
    setBrowseBusy(false);
    setActiveRaw('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  async function generate() {
    const text = raw.trim();
    if (!text) {
      setError('Paste a ticket first.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await postDeclaration(text);
      showResult(res, null, text);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function fetchJira(key) {
    const k = (key || issueKey).trim();
    if (!k) {
      setError('Enter a Jira issue key or link.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await jiraFetch(k);
      const { jira, ...pipeline } = res;
      const meta = jira
        ? { key: jira.key, summary: jira.summary }
        : { key: k, summary: '' };
      // The fetched description is kept as this result's source text so a
      // candidate pick / manual UUID can re-run the pipeline on it.
      showResult(pipeline, meta, (jira && jira.description) || '');
      // Show the canonical key the server resolved (a pasted link becomes its key).
      setIssueKey(meta.key || k);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function browse() {
    if (browseOpen) {
      setBrowseOpen(false);
      return;
    }
    setBrowseOpen(true);
    setBrowseBusy(true);
    setError('');
    try {
      const res = await jiraSearch(); // default JQL from settings
      setIssues(res.issues || []);
    } catch (e) {
      setError(e.message);
      setIssues([]);
    } finally {
      setBrowseBusy(false);
    }
  }

  // Re-run the pipeline with an operator-chosen company_uuid: re-POST the
  // result's OWN source text (the generated paste or the Jira-fetched
  // description) through /api/declaration. Never fall back to the paste box —
  // it may still hold a previous case's text.
  async function repick(uuid) {
    if (!uuid) return;
    const text = activeRaw.trim();
    if (!text) {
      setError(
        'No source text for this result — fetch the ticket again (or paste it), then pick the account.'
      );
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await postDeclaration(text, uuid);
      showResult(res, jiraMeta, text);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const parsed = result?.parsed;
  const caseRef = parsed?.case_references;
  const pendingSelection = result?.status === 'pending_selection';
  const routedOut = result?.scenario === 'ROUTED_OUT';
  // Review warnings worth a banner (the parsed warnings render separately).
  const pipelineWarnings = (result?.warnings || []).filter(
    (w) => !w.startsWith('halted:')
  );

  return (
    <>
      {/* Input card */}
      <div className="card">
        <div className="card-head">
          <h2>Input</h2>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn small"
              onClick={() => setPasteOpen((v) => !v)}
              title="Show or hide the manual paste box"
            >
              {pasteOpen ? 'Hide paste box' : 'Show paste box'}
            </button>
            <button
              className="btn small"
              onClick={resetCase}
              disabled={busy}
              title="Clear the ticket and result and start a new case"
            >
              ↻ New case
            </button>
          </div>
        </div>

        {pasteOpen && (
          <>
            <div className="field">
              <label htmlFor="raw">Paste seizure ticket</label>
              <textarea
                id="raw"
                rows={8}
                placeholder="Paste the German seizure ticket text here…"
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
              />
            </div>
            <button className="btn primary" disabled={busy} onClick={generate}>
              {busy ? <span className="spinner" /> : null} Generate
            </button>

            <hr className="divider" />
          </>
        )}

        <div className="inline">
          <div className="grow">
            <label htmlFor="issue">Fetch from Jira (issue key or link)</label>
            <input
              id="issue"
              type="text"
              placeholder="e.g. SEIZ-1234 or https://…/browse/SEIZ-1234"
              value={issueKey}
              onChange={(e) => setIssueKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchJira()}
            />
          </div>
          <button className="btn" disabled={busy} onClick={() => fetchJira()}>
            Fetch
          </button>
          <button className="btn" disabled={browseBusy} onClick={browse}>
            {browseOpen ? 'Hide browse' : 'Browse'}
          </button>
        </div>

        {browseOpen && (
          <div style={{ marginTop: 12 }}>
            {browseBusy && (
              <div className="muted small">
                <span className="spinner" /> Loading issues…
              </div>
            )}
            {!browseBusy && issues && issues.length === 0 && (
              <div className="muted small">No issues found.</div>
            )}
            {!browseBusy && issues && issues.length > 0 && (
              <div className="cand-list">
                {issues.map((it) => (
                  <div className="cand" key={it.key}>
                    <div className="info">
                      <div className="name">{it.summary || '(no summary)'}</div>
                      <div className="meta">{it.key}</div>
                    </div>
                    <button
                      className="btn small primary"
                      disabled={busy}
                      onClick={() => fetchJira(it.key)}
                    >
                      Fetch
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {error && <div className="banner err">{error}</div>}

      {/* Results */}
      {result && (
        <>
          {jiraMeta && (
            <div className="context-strip">
              <span className="key">{jiraMeta.key}</span>
              {jiraMeta.summary && <span className="summary">{jiraMeta.summary}</span>}
            </div>
          )}

          {parsed && (parsed.halted || (parsed.warnings && parsed.warnings.length > 0)) && (
            <div className={`banner ${parsed.halted ? 'err' : 'warn'}`}>
              {parsed.halted && (
                <div>
                  <strong>Parsing halted:</strong>{' '}
                  {(parsed.halt_reasons || []).join('; ')}
                </div>
              )}
              {parsed.warnings && parsed.warnings.length > 0 && (
                <div>{parsed.warnings.join('; ')}</div>
              )}
            </div>
          )}

          {pipelineWarnings.length > 0 && result.status === 'ok' && (
            <div className="banner warn">
              {pipelineWarnings.map((w, i) => (
                <div key={i}>{w}</div>
              ))}
            </div>
          )}

          {/* Document is the hero (left); reference panels sit in a sticky
              rail on the right — scenario, account, alerts, balance, seizure
              check, then the raw parsed fields last. */}
          <div className="results-grid">
            {result.declaration ? (
              <DeclarationEditor
                declaration={result.declaration}
                caseRef={caseRef}
                onToast={setToast}
              />
            ) : (
              <div className="hero">
                <div className="hero-head">
                  <span className="hero-title">
                    {pendingSelection
                      ? 'Waiting for account selection'
                      : routedOut
                        ? 'Routed out — manual handling'
                        : 'No document'}
                  </span>
                  <span className="badge warn">
                    {pendingSelection ? 'pending' : routedOut ? 'operator' : '—'}
                  </span>
                </div>
                <div className="muted" style={{ padding: '12px 4px' }}>
                  {pendingSelection && (
                    <>
                      The account could not be uniquely resolved. Pick one of the
                      candidates in the Account panel (or enter a company UUID) to
                      resolve the scenario and generate the document.
                    </>
                  )}
                  {routedOut && (
                    <>
                      {result.plan?.rationale}
                      {result.plan?.notes?.length > 0 && (
                        <div style={{ marginTop: 8 }}>
                          {result.plan.notes.map((n, i) => (
                            <div key={i}>· {n}</div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                  {!pendingSelection && !routedOut && (
                    <>No customer document is generated for this case.</>
                  )}
                </div>
              </div>
            )}

            <div className="rail">
              <ScenarioPanel scenario={result.scenario} plan={result.plan} />
              <AccountPanel account={result.account} onPick={repick} busy={busy} />
              <AlertsPanel alerts={result.alerts} />
              <BalancePanel balance={result.balance} />
              <SeizureCheck check={result.seizure_check} />
              <FieldTable parsed={parsed} />
            </div>
          </div>
        </>
      )}

      <Toast message={toast} onClose={() => setToast('')} />
    </>
  );
}
