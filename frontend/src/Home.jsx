import React, { useRef, useState } from 'react';
import { postCompose, postDeclaration, jiraFetch, jiraSearch } from './api.js';
import DecisionPanel from './components/DecisionPanel.jsx';
import WorkBar from './components/WorkBar.jsx';
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

  // Manual mode (docs/manual-mode-plan.md): auto proposes, operator disposes.
  // `decisions` is the operator-editable copy; `autoDecisions` stays pristine
  // for the override badge; edits recompose the document live (debounced).
  const [manualOn, setManualOn] = useState(false);
  const [decisions, setDecisions] = useState(null);
  const [autoDecisions, setAutoDecisions] = useState(null);
  // The auto pipeline's document, kept pristine so switching manual mode OFF
  // restores it (recomposes overwrite result.declaration).
  const [autoDeclaration, setAutoDeclaration] = useState(null);
  const [composeWarnings, setComposeWarnings] = useState([]);
  const composeTimer = useRef(null);

  // Async-work indicator (WorkBar + status chip). `working` carries the
  // current stage label; `workHost` says which card hosts the bar ('input'
  // for first runs, 'doc' for recomposes/re-runs). A single HTTP call has no
  // real stage events, so the label CYCLES through the server's known stage
  // order (~1.3s each, holding on the last) — honest about order, not exact
  // timing. Turns on only when a call exceeds 250ms (anti-flicker); a
  // successful finish on the document card flashes "updated" for ~2s.
  const [working, setWorking] = useState(null);
  const [workHost, setWorkHost] = useState('doc');
  const [flash, setFlash] = useState(false);
  const flashTimer = useRef(null);
  const stageTimer = useRef(null);

  async function tracked(labels, fn, host = 'doc') {
    const stages = Array.isArray(labels) ? labels : [labels];
    let i = 0;
    const delay = setTimeout(() => {
      setWorkHost(host);
      setWorking(stages[0]);
      stageTimer.current = setInterval(() => {
        i = Math.min(i + 1, stages.length - 1);
        setWorking(stages[i]);
        if (i === stages.length - 1) clearInterval(stageTimer.current);
      }, 1300);
    }, 250);
    let ok = false;
    try {
      const out = await fn();
      ok = true;
      return out;
    } finally {
      clearTimeout(delay);
      clearInterval(stageTimer.current);
      setWorking(null);
      if (ok && host === 'doc') {
        setFlash(true);
        clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setFlash(false), 2200);
      }
    }
  }

  const PIPELINE_STAGES = [
    'parsing ticket fields…',
    'identifying the account in BO…',
    'running checks — alerts · seizures · balance…',
    'resolving the scenario…',
    'composing the document…',
  ];

  const showResult = (res, meta, srcRaw) => {
    setResult(res);
    setJiraMeta(meta || null);
    setActiveRaw(srcRaw);
    setManualOn(false);
    setDecisions(res?.manual?.decisions || null);
    setAutoDecisions(res?.manual?.decisions || null);
    setAutoDeclaration(res?.declaration || null);
    setComposeWarnings([]);
    if (composeTimer.current) clearTimeout(composeTimer.current);
  };

  // Manual OFF = back to auto: decisions reset to the pristine copy and the
  // auto document replaces any recomposed one. Manual ON starts from auto.
  function toggleManual() {
    setManualOn((v) => {
      const next = !v;
      if (!next) {
        if (composeTimer.current) clearTimeout(composeTimer.current);
        setDecisions(autoDecisions);
        setComposeWarnings([]);
        setResult((r) => (r ? { ...r, declaration: autoDeclaration } : r));
      }
      return next;
    });
  }

  // Debounced live recompose: PURE backend call — no BO re-fetch, no pipeline
  // re-run; just the decision set turned back into a document.
  function updateDecisions(next) {
    setDecisions(next);
    if (composeTimer.current) clearTimeout(composeTimer.current);
    composeTimer.current = setTimeout(() => recompose(next), 500);
  }

  async function recompose(d) {
    const m = result?.manual;
    if (!m || !d?.template) return;
    try {
      const res = await tracked(`recomposing — ${d.template}…`, () =>
        postCompose({
          decisions: d,
          context: { ...(m.context || {}), options: m.options || {} },
          auto: m.auto,
        })
      );
      setResult((r) => ({ ...r, declaration: res.declaration }));
      setComposeWarnings(res.warnings || []);
    } catch (e) {
      setComposeWarnings([`compose failed: ${e.message}`]);
    }
  }

  // Manual mode's parsed-field editing: re-runs the WHOLE pipeline (the edit
  // may change identification/checks), keeping the resolved account.
  async function rerunWithFields(overrides) {
    const text = activeRaw.trim();
    if (!text) {
      setError('No source text for this result — fetch or paste the ticket again.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const uuid = result?.account?.company_uuid || undefined;
      const res = await tracked(PIPELINE_STAGES, () =>
        postDeclaration(text, uuid, false, overrides)
      );
      showResult(res, jiraMeta, text);
      setManualOn(true); // the operator was clearly working manually
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

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
    setManualOn(false);
    setDecisions(null);
    setAutoDecisions(null);
    setAutoDeclaration(null);
    setComposeWarnings([]);
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
      const res = await tracked(PIPELINE_STAGES, () => postDeclaration(text), 'input');
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
      const res = await tracked(
        ['fetching the ticket from Jira…', ...PIPELINE_STAGES],
        () => jiraFetch(k), 'input');
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
    await rerunWith({ uuid });
  }

  // Operator's "none of these" declaration -> forces NO_MATCH (Scenario 4).
  async function declareNoMatch() {
    await rerunWith({ noMatch: true });
  }

  async function rerunWith({ uuid, noMatch }) {
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
      const res = await tracked(PIPELINE_STAGES, () =>
        postDeclaration(text, uuid, noMatch)
      );
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
  // FYI-grade notes move into the panels' ⓘ badges instead of full-width
  // banners: identification/parse notes -> Account, non-EUR note -> Balance.
  const allPipeline = (result?.warnings || []).filter((w) => !w.startsWith('halted:'));
  const accountNotes = [
    ...(parsed?.warnings || []),
    ...allPipeline.filter((w) => w.startsWith('workspaces') || w.includes('searched across workspaces')),
  ];
  const balanceNotes = allPipeline.filter((w) => w.toLowerCase().includes('non-eur'));
  // Everything else stays a visible banner (resolver notes, amount fallbacks…).
  const pipelineWarnings = allPipeline.filter(
    (w) => !accountNotes.includes(w) && !balanceNotes.includes(w)
  );

  return (
    <>
      {/* Input card */}
      <div className="card" style={{ position: 'relative' }}>
        <WorkBar active={workHost === 'input' && !!working} />
        <div className="card-head">
          <h2>Input</h2>
          {workHost === 'input' && working && (
            <span className="badge" style={{ color: '#ec4899', borderColor: '#5a3246' }}>
              ⟳ {working}
            </span>
          )}
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
          {(jiraMeta || result.manual) && (
            <div className="context-strip">
              {jiraMeta && <span className="key">{jiraMeta.key}</span>}
              {jiraMeta?.summary && <span className="summary">{jiraMeta.summary}</span>}
              {result.manual && (
                <button
                  className={`btn small${manualOn ? ' primary' : ''}`}
                  style={{ marginLeft: 'auto' }}
                  onClick={toggleManual}
                  title="Unlock the decision set: template, seizure roles, amounts, IBAN, email slots. Turning it off reverts to auto."
                >
                  {manualOn ? 'Manual mode: on' : 'Manual mode'}
                </button>
              )}
            </div>
          )}

          {parsed?.halted && (
            <div className="banner err">
              <div>
                <strong>Parsing halted:</strong>{' '}
                {(parsed.halt_reasons || []).join('; ')}
              </div>
              {parsed.warnings && parsed.warnings.length > 0 && (
                <div>{parsed.warnings.join('; ')}</div>
              )}
            </div>
          )}
          {/* No Account panel to host the ⓘ badge (halted/criminal) -> keep
              the identification notes visible as a banner. */}
          {!parsed?.halted && !result.account && accountNotes.length > 0 && (
            <div className="banner warn">
              {accountNotes.map((w, i) => (
                <div key={i}>{w}</div>
              ))}
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
                working={workHost === 'doc' ? working : null}
                flash={flash}
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
              {manualOn && (
                <DecisionPanel
                  decisions={decisions}
                  autoDecisions={autoDecisions}
                  manual={result.manual}
                  warnings={composeWarnings}
                  busy={busy}
                  onChange={updateDecisions}
                />
              )}
              <ScenarioPanel
                scenario={result.scenario}
                plan={result.plan}
                manualTemplate={
                  manualOn &&
                  decisions?.template &&
                  decisions.template !== result.manual?.auto?.template
                    ? decisions.template
                    : ''
                }
              />
              <AccountPanel account={result.account} onPick={repick}
                            onNoMatch={declareNoMatch} busy={busy}
                            notes={accountNotes} />
              <AlertsPanel alerts={result.alerts} />
              <BalancePanel balance={result.balance} notes={balanceNotes} />
              <SeizureCheck check={result.seizure_check} />
              <FieldTable parsed={parsed} editable={manualOn} busy={busy}
                          onApply={rerunWithFields} />
            </div>
          </div>
        </>
      )}

      <Toast message={toast} onClose={() => setToast('')} />
    </>
  );
}
