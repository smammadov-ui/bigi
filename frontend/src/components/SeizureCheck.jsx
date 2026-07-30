import React from 'react';

// Ongoing (Processing) seizures check (rail card): competing count drives the
// S1/S2 split. Also surfaces the ignored-seizure notes (own case,
// created-after-this-case) and any error / "assumed" state from the BO check.
export default function SeizureCheck({ check }) {
  if (!check) return null;
  const n = check.processing_count || 0;
  const seizures = check.seizures || [];

  return (
    <div className="rail-card">
      <div className="rail-head">
        <span className="rail-label">Seizure check</span>
        <span className={`badge ${n > 0 ? 't2' : 't1'}`}>
          {n > 0 ? `${n} ongoing` : 'none ongoing'}
        </span>
      </div>

      {check.error && (
        <div className="banner err">
          {check.assumed ? 'Assumed none — ' : ''}
          {check.error}
        </div>
      )}
      {!check.error && check.assumed && (
        <div className="banner warn">Assumed none (check not conclusive).</div>
      )}
      {!check.error && !check.assumed && check.own_case_missing && (
        <div className="banner warn">
          This ticket's own seizure was not found in BO — was it already submitted?
          The seized amount falls back to min(claim, balance).
        </div>
      )}

      {check.ignored_same_case && check.ignored_same_case.length > 0 && (
        <div className="banner info">
          Ignored this ticket's own case ({check.ignored_same_case.map((s) => s.caseNumber || s.id).join(', ')}).
        </div>
      )}
      {check.ignored_later && check.ignored_later.length > 0 && (
        <div className="banner info">
          Ignored {check.ignored_later.length} seizure
          {check.ignored_later.length === 1 ? '' : 's'} created after this case
          ({check.ignored_later.map((s) => s.caseNumber || s.id).join(', ')}).
        </div>
      )}

      {seizures.length > 0 ? (
        <>
          <div className="seiz-count">{check.processing_count} ongoing (Processing)</div>
          {seizures.map((s) => (
            <div className="seiz-item" key={s.id}>
              <div className="case">{s.caseNumber || s.id}</div>
              <div className="meta">
                {s.status}
                {s.created ? ` · ${s.created}` : ''}
              </div>
              {s.comment && <div className="who">{s.comment}</div>}
            </div>
          ))}
        </>
      ) : (
        !check.error && <div className="seiz-count">no ongoing seizures</div>
      )}
    </div>
  );
}
