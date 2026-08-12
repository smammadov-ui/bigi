import React from 'react';

// Manual mode's control surface (docs/manual-mode-plan.md): the operator edits
// the DECISION SET — template, per-seizure roles, amounts, seized IBAN, email
// slots — and Home recomposes the document live. Auto's choices stay visible
// ("(auto)" marker, auto_role hints); contradictions arrive as non-blocking
// warnings from the backend validator.

const ROLE_LABEL = { own: 'own case', report: 'report', ignore: 'ignore' };

function groupTemplates(catalog) {
  const groups = new Map();
  for (const t of catalog || []) {
    if (!groups.has(t.family)) groups.set(t.family, []);
    groups.get(t.family).push(t);
  }
  return [...groups.entries()];
}

// Editable number input: keeps the raw string while typing; empty -> null.
function NumField({ label, value, onChange }) {
  return (
    <div className="field" style={{ marginBottom: 8 }}>
      <label>{label}</label>
      <input
        type="text"
        inputMode="decimal"
        value={value ?? ''}
        placeholder="—"
        onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
      />
    </div>
  );
}

export default function DecisionPanel({
  decisions,        // the editable decision set
  autoDecisions,    // pristine auto copy (override badge)
  manual,           // result.manual: { auto, options, context }
  warnings,         // last recompose validation warnings
  busy,
  onChange,         // (nextDecisions) => void  (Home debounces + recomposes)
}) {
  if (!decisions || !manual) return null;
  const options = manual.options || {};
  const auto = manual.auto || {};
  const catalog = options.templates || [];
  const selected = catalog.find((t) => t.id === decisions.template);
  const kind = selected?.kind || '';

  const set = (patch) => onChange({ ...decisions, ...patch });
  const setRow = (id, patch) =>
    set({
      seizures: (decisions.seizures || []).map((r) =>
        r.id === id ? { ...r, ...patch } : r
      ),
    });

  // Override badge: which decision groups deviate from auto.
  const overrides = [];
  if (autoDecisions) {
    if (decisions.template !== autoDecisions.template) overrides.push('template');
    const roleDiff = (decisions.seizures || []).filter(
      (r, i) => r.role !== (autoDecisions.seizures || [])[i]?.role
    ).length;
    if (roleDiff) overrides.push(`roles (${roleDiff})`);
    for (const k of ['own_case_amount', 'available_eur', 'seizable_eur']) {
      if (String(decisions[k] ?? '') !== String(autoDecisions[k] ?? '')) {
        overrides.push('amounts');
        break;
      }
    }
    if (decisions.seized_iban?.value !== autoDecisions.seized_iban?.value)
      overrides.push('IBAN');
    if (
      decisions.subject !== autoDecisions.subject ||
      decisions.recipient_email !== autoDecisions.recipient_email
    )
      overrides.push('email');
  }

  return (
    <div className="rail-card" style={{ borderColor: 'var(--accent, #ec4899)' }}>
      <div className="rail-head">
        <span className="rail-label">Decision (manual mode)</span>
        <span className={`badge ${overrides.length ? 'warn' : 't1'}`}>
          {overrides.length ? `overrides: ${overrides.join(', ')}` : 'auto values'}
        </span>
      </div>

      {/* Template */}
      <div className="field" style={{ marginBottom: 8 }}>
        <label>Template</label>
        <select
          value={decisions.template || ''}
          disabled={busy}
          onChange={(e) => set({ template: e.target.value })}
        >
          <option value="">— pick a template —</option>
          {groupTemplates(catalog).map(([family, items]) => (
            <optgroup key={family} label={family}>
              {items.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                  {t.id === auto.template ? '  (auto)' : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {/* Needs checklist for the chosen template */}
      {selected && selected.needs.length > 0 && (
        <div className="muted small" style={{ marginBottom: 8 }}>
          {selected.id} needs: {selected.needs.join(' · ')}
        </div>
      )}

      {/* Email slots */}
      {(kind === 'email' || kind === 'guidance') && (
        <div className="field" style={{ marginBottom: 8 }}>
          <label>Recipient email</label>
          <input
            type="text"
            value={decisions.recipient_email || ''}
            placeholder="creditor email"
            onChange={(e) => set({ recipient_email: e.target.value })}
          />
        </div>
      )}
      <div className="field" style={{ marginBottom: 8 }}>
        <label>Subject</label>
        <input
          type="text"
          value={decisions.subject || ''}
          placeholder="(auto-derived from the template)"
          onChange={(e) => set({ subject: e.target.value })}
        />
      </div>

      {/* Seizure roles */}
      {(decisions.seizures || []).length > 0 && (
        <>
          <div className="rail-label" style={{ margin: '10px 0 6px' }}>
            Seizures — what the letter reports
          </div>
          {decisions.seizures.map((r) => (
            <div className="seiz-item" key={r.id}>
              <div className="case">{r.case_ref || r.id}</div>
              <div className="meta">
                {r.note}
                {r.role !== r.auto_role ? ` · auto: ${ROLE_LABEL[r.auto_role]}` : ''}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                <select
                  value={r.role}
                  disabled={busy}
                  style={{ flex: 1 }}
                  onChange={(e) => setRow(r.id, { role: e.target.value })}
                >
                  <option value="own">own case</option>
                  <option value="report">report in the letter</option>
                  <option value="ignore">ignore</option>
                </select>
                <input
                  type="text"
                  inputMode="decimal"
                  style={{ width: 110 }}
                  value={r.amount ?? ''}
                  placeholder="amount €"
                  onChange={(e) =>
                    setRow(r.id, {
                      amount: e.target.value === '' ? null : e.target.value,
                    })
                  }
                />
              </div>
            </div>
          ))}
        </>
      )}

      {/* Amounts */}
      <div className="rail-label" style={{ margin: '10px 0 6px' }}>
        Amounts (EUR)
      </div>
      <NumField
        label="Seizable — printed as [Seized amount]"
        value={decisions.seizable_eur}
        onChange={(v) => set({ seizable_eur: v })}
      />
      <NumField
        label="Own-case amount (reference)"
        value={decisions.own_case_amount}
        onChange={(v) => set({ own_case_amount: v })}
      />
      <NumField
        label="Available balance (reference)"
        value={decisions.available_eur}
        onChange={(v) => set({ available_eur: v })}
      />

      {/* Seized IBAN */}
      {(options.wallets || []).length > 0 && (
        <div className="field" style={{ marginBottom: 8 }}>
          <label>Seized IBAN (wallet)</label>
          <select
            value={decisions.seized_iban?.value || ''}
            disabled={busy}
            onChange={(e) =>
              set({ seized_iban: { value: e.target.value, source: 'manual' } })
            }
          >
            {decisions.seized_iban?.value &&
              !options.wallets.some(
                (w) => w.iban === decisions.seized_iban.value
              ) && (
                <option value={decisions.seized_iban.value}>
                  {decisions.seized_iban.value} (from ticket)
                </option>
              )}
            {options.wallets.map((w) => (
              <option key={w.iban} value={w.iban}>
                {w.name || 'wallet'} — {w.iban}
                {w.currency && w.currency !== 'EUR' ? ` (${w.currency})` : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Validator output (non-blocking, passive cross-hints included) */}
      {warnings && warnings.length > 0 && (
        <div className="banner warn" style={{ marginTop: 8 }}>
          {warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}
    </div>
  );
}
