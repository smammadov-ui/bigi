import React, { useState } from 'react';

// Shown when the account could not be uniquely resolved. Either pick one of the
// returned cstools candidates or type a company UUID by hand; either way we
// re-run the pipeline with the chosen company_uuid.
export default function CandidatePicker({ candidates, onPick, busy }) {
  const [manual, setManual] = useState('');
  const list = candidates || [];

  return (
    <div>
      {list.length > 0 && (
        <>
          <div className="muted small" style={{ marginBottom: 8 }}>
            Multiple matches — pick the right account:
          </div>
          <div className="cand-list">
            {list.map((c) => (
              <div className="cand" key={c.id}>
                <div className="info">
                  <div className="name">{c.businessName || '(no name)'}</div>
                  <div className="meta">
                    {c.id}
                    {c.regNumber ? ` · ${c.regNumber}` : ''}
                  </div>
                </div>
                <button
                  className="btn small primary"
                  disabled={busy}
                  onClick={() => onPick(c.id)}
                >
                  Use
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <hr className="divider" />

      <div className="inline">
        <div className="grow">
          <label htmlFor="manual-uuid">Or enter a company UUID manually</label>
          <input
            id="manual-uuid"
            type="text"
            placeholder="company_uuid"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
          />
        </div>
        <button
          className="btn primary"
          disabled={busy || !manual.trim()}
          onClick={() => onPick(manual.trim())}
        >
          Re-run with UUID
        </button>
      </div>
    </div>
  );
}
