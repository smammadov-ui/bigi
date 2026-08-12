import React, { useState } from 'react';

// A small ⓘ chip for rail-card headers that hides informational notes behind
// a click (the notes used to be full-width banners above the results — noisy
// for what is effectively FYI text). Click toggles a right-aligned popover.
export default function InfoBadge({ notes }) {
  const [open, setOpen] = useState(false);
  const list = (notes || []).filter(Boolean);
  if (list.length === 0) return null;

  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        className="badge"
        onClick={() => setOpen((v) => !v)}
        title={open ? 'Hide notes' : `${list.length} note${list.length === 1 ? '' : 's'}`}
        style={{
          cursor: 'pointer',
          background: open ? '#2c3440' : 'transparent',
          border: '1px solid #2c3440',
          color: '#aeb6c2',
          lineHeight: 1.2,
        }}
      >
        ⓘ {list.length}
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 6px)',
            zIndex: 30,
            width: 300,
            maxWidth: '72vw',
            background: '#171c23',
            border: '1px solid #2c3440',
            borderRadius: 8,
            padding: '10px 12px',
            boxShadow: '0 8px 24px rgba(0,0,0,.45)',
            fontSize: 12,
            color: '#aeb6c2',
          }}
        >
          {list.map((n, i) => (
            <div key={i} style={{ margin: i ? '8px 0 0' : 0 }}>
              · {n}
            </div>
          ))}
        </div>
      )}
    </span>
  );
}
