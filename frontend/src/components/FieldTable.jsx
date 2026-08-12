import React, { useEffect, useState } from 'react';

// Render parsed.fields as a compact key/value list in the rail. Skips the meta
// keys (warnings / halted / halt_reasons / edited_fields) which are surfaced
// separately. Keys are shown verbatim (mono) — they mirror the pipeline's
// field names.
//
// Manual mode (`editable`): every field becomes an input; "Re-run with edited
// fields" posts the CHANGED keys as field_overrides — that re-runs the whole
// pipeline (identification + checks), unlike decision edits which only
// recompose the document.
const META = new Set(['warnings', 'halted', 'halt_reasons', 'edited_fields']);

export default function FieldTable({ parsed, editable = false, busy = false, onApply }) {
  const [edits, setEdits] = useState({});
  // Minimized by default — the long key/value list is reference material.
  // Manual mode auto-expands it (the fields are the editing surface there).
  const [open, setOpen] = useState(false);

  // New result -> drop stale edits.
  useEffect(() => {
    setEdits({});
  }, [parsed]);

  useEffect(() => {
    if (editable) setOpen(true);
  }, [editable]);

  if (!parsed) return null;
  const keys = Object.keys(parsed).filter((k) => !META.has(k));
  const editedKeys = Object.keys(edits).filter(
    (k) => String(parsed[k] ?? '') !== String(edits[k] ?? '')
  );
  const wasEdited = new Set(parsed.edited_fields || []);

  const apply = () => {
    if (!onApply || editedKeys.length === 0) return;
    const overrides = {};
    for (const k of editedKeys) overrides[k] = edits[k];
    onApply(overrides);
  };

  return (
    <div className="rail-card">
      <div className="rail-head">
        <span className="rail-label">Parsed fields</span>
        <button
          type="button"
          className="badge"
          onClick={() => setOpen((v) => !v)}
          title={open ? 'Minimize' : 'Expand'}
          style={{ cursor: 'pointer', background: 'transparent',
                   border: '1px solid #2c3440', color: '#aeb6c2' }}
        >
          {open ? '▾ hide' : `▸ ${keys.length} fields`}
        </button>
      </div>
      {open && (
      <div className="pf-list">
        {keys.map((k) => {
          const v = parsed[k];
          const empty = v === '' || v == null || (Array.isArray(v) && v.length === 0);
          const text = Array.isArray(v) ? v.join(', ') : String(v ?? '');
          if (editable) {
            const val = k in edits ? edits[k] : text;
            return (
              <div key={k}>
                <div className="pk">
                  {k}
                  {wasEdited.has(k) ? ' ✎' : ''}
                </div>
                <input
                  type="text"
                  value={val}
                  placeholder="—"
                  style={{ width: '100%', marginTop: 2 }}
                  onChange={(e) => setEdits((s) => ({ ...s, [k]: e.target.value }))}
                />
              </div>
            );
          }
          return (
            <div key={k}>
              <div className="pk">
                {k}
                {wasEdited.has(k) ? ' ✎ (operator-edited)' : ''}
              </div>
              <div className={`pv${empty ? ' empty' : ''}`}>{empty ? '—' : text}</div>
            </div>
          );
        })}
      </div>
      )}
      {open && editable && (
        <button
          className="btn small primary"
          style={{ marginTop: 10 }}
          disabled={busy || editedKeys.length === 0}
          onClick={apply}
          title="Re-runs identification + checks with the corrected fields"
        >
          Re-run with edited fields{editedKeys.length ? ` (${editedKeys.length})` : ''}
        </button>
      )}
    </div>
  );
}
