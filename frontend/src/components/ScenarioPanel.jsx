import React from 'react';

// Human labels for the 11 coded scenarios.
const SCENARIO_LABEL = {
  S1: 'S1 — Normal TPD',
  S2: 'S2 — Prior ongoing seizures',
  S3: 'S3 — Closed / onboarding',
  S4_NO_IBAN: 'S4 — No match (no IBAN)',
  S4_IBAN: 'S4 — No match (IBAN unknown)',
  S5: 'S5 — Person vs. company',
  S6A: 'S6A — Closing, covered',
  S6B: 'S6B — Closing, balance left',
  INSOLVENCY: 'Insolvency (MNL21)',
  RFI: 'Information request (MNL22)',
  ROUTED_OUT: 'Routed out — manual handling',
};

const ACTION_LABEL = {
  letter: '§840 letter',
  email: 'email',
  data_gathering: 'data gathering',
  operator: 'operator',
};

// The resolved scenario + plan (rail card): code, action, rationale, and any
// degradation notes from the resolver. Shown once a scenario is decided.
export default function ScenarioPanel({ scenario, plan, manualTemplate }) {
  if (!scenario || !plan) return null;
  const isOperator = plan.action === 'operator';
  const badgeCls = manualTemplate
    ? 'warn'
    : isOperator ? 'warn' : plan.action === 'letter' ? 't1' : 't2';

  return (
    <div className="rail-card">
      <div className="rail-head">
        <span className="rail-label">Scenario</span>
        <span className={`badge ${badgeCls}`}>
          {manualTemplate
            ? `${manualTemplate} — manual`
            : plan.template || ACTION_LABEL[plan.action]}
        </span>
      </div>

      {manualTemplate && (
        <div className="muted small" style={{ marginBottom: 6 }}>
          auto: {scenario}/{plan.template || '—'} — operator overrode the template
        </div>
      )}

      <div className="rail-name">{SCENARIO_LABEL[scenario] || scenario}</div>
      <div className="rail-note">
        action: {ACTION_LABEL[plan.action] || plan.action}
      </div>

      <div className="muted small" style={{ marginTop: 8 }}>
        {plan.rationale}
      </div>

      {plan.notes && plan.notes.length > 0 && (
        <div className="banner warn" style={{ marginTop: 8 }}>
          {plan.notes.join(' · ')}
        </div>
      )}
    </div>
  );
}
