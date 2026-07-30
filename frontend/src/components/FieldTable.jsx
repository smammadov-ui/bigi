import React from 'react';

// Render parsed.fields as a compact key/value list in the rail. Skips the meta
// keys (warnings / halted / halt_reasons) which are surfaced separately. Keys
// are shown verbatim (mono) — they mirror the pipeline's field names.
const META = new Set(['warnings', 'halted', 'halt_reasons']);

export default function FieldTable({ parsed }) {
  if (!parsed) return null;
  const keys = Object.keys(parsed).filter((k) => !META.has(k));

  return (
    <div className="rail-card">
      <div className="rail-label">Parsed fields</div>
      <div className="pf-list">
        {keys.map((k) => {
          const v = parsed[k];
          const empty = v === '' || v == null || (Array.isArray(v) && v.length === 0);
          const text = Array.isArray(v) ? v.join(', ') : String(v ?? '');
          return (
            <div key={k}>
              <div className="pk">{k}</div>
              <div className={`pv${empty ? ' empty' : ''}`}>{empty ? '—' : text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
