import React from 'react';

const RULE_LABEL = {
  MNL20: 'MNL20 — seizure mechanism',
  MNL21: 'MNL21 — insolvency',
  MNL22: 'MNL22 — information request',
};

// Open transaction-monitoring alerts (rail card). Open MNL21/MNL22 branch the
// scenario; any other open rule routes the case to the operator.
export default function AlertsPanel({ alerts }) {
  if (!alerts) return null;
  const open = alerts.open_rules || [];

  return (
    <div className="rail-card">
      <div className="rail-head">
        <span className="rail-label">Alerts</span>
        <span className={`badge ${open.length ? 'warn' : 'ok'}`}>
          {open.length ? `${open.length} open` : 'none open'}
        </span>
      </div>

      {alerts.error && (
        <div className="banner err">
          <div>
            {alerts.assumed ? 'Assumed none — ' : ''}
            {alerts.error}
          </div>
        </div>
      )}

      {open.length > 0 && (
        <div className="pf-list">
          {open.map((r) => (
            <div key={r}>
              <div className="pk">{r}</div>
              <div className="pv">{RULE_LABEL[r] || 'other open alert → operator review'}</div>
            </div>
          ))}
        </div>
      )}

      {!alerts.error && open.length === 0 && (
        <div className="muted small">
          {alerts.total > 0
            ? `${alerts.total} alert(s) on the account — all resolved.`
            : 'No alerts on the account.'}
        </div>
      )}
    </div>
  );
}
