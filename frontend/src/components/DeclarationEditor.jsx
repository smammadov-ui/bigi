import React, { useEffect, useRef, useState } from 'react';
import WorkBar from './WorkBar.jsx';

const COMPOSED_LABEL = {
  'llm:openai': 'LLM (openai)',
  'llm:anthropic': 'LLM (anthropic)',
  deterministic: 'deterministic fill',
};

// Heading labels rendered as the letter's header grid, and bolded in the
// rich-text clipboard flavor (Gmail, Word, Jira …).
const BOLD_LABELS = [
  'Gläubiger:',
  'Schuldner:',
  'Zustellungsdatum bei uns:',
  'Pfändungsbetrag:',
];

// A rendered ongoing-seizure bullet line: a leading tab then the bullet glyph.
// The legacy "*" marker is tolerated so older declarations still render.
const BULLET_RE = /^\t[•*]\s*/;

// The seized-amount sentence — its amount comes from Back-Office.
const SEIZED_RE = /^(Die gepfändete Forderung besteht derzeit in Höhe von )(.+)( EUR\.)$/;

const escapeHtml = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// One non-bullet line -> escaped text with its heading label bolded.
function lineToHtml(line) {
  const label = BOLD_LABELS.find((l) => line.startsWith(l));
  if (!label) return escapeHtml(line);
  return `<strong>${escapeHtml(label)}</strong>${escapeHtml(line.slice(label.length))}`;
}

// HTML twin of the plain-text letter for the clipboard: bold labels, bullet
// runs as a real <ul>, everything wrapped in an explicit near-black color so it
// pastes readable on the white background of an email compose window (the macOS
// Tauri webview otherwise bakes in the dark-theme text color -> white on white).
// The on-screen source underlines are a UI-only affordance, NOT part of the sent
// letter, so the copied HTML stays clean.
function toRichHtml(text) {
  const lines = text.split('\n');
  const parts = [];
  let i = 0;
  while (i < lines.length) {
    if (BULLET_RE.test(lines[i])) {
      const items = [];
      while (i < lines.length && BULLET_RE.test(lines[i])) {
        items.push(`<li>${escapeHtml(lines[i].replace(BULLET_RE, ''))}</li>`);
        i++;
      }
      parts.push(`<ul style="margin:0;padding-left:1.5em">${items.join('')}</ul>`);
    } else {
      parts.push(lineToHtml(lines[i]));
      i++;
    }
  }
  return `<div style="color:#111111">${parts.join('<br>')}</div>`;
}

// A filesystem-safe slug from the case reference (shared by the .txt and .pdf
// exports).
function slugify(caseRef) {
  return (
    (caseRef || 'declaration')
      .toString()
      .replace(/[^\w-]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .toLowerCase() || 'declaration'
  );
}

// Download a blob via a synthetic anchor — the one clipboard/file path that
// works in the Tauri WKWebView (native save/print dialogs do not).
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Render the letter to a real, text-based PDF (selectable text, not an image)
// with pdf-lib. Times (a standard font) uses WinAnsi encoding, which covers all
// letter glyphs — German umlauts/ß, §, € and the • bullet — so no font file is
// bundled. Layout mirrors the on-screen document: bold subject, bold header
// labels with regular values, wrapped prose, and hanging-indent bullets, on A4
// with automatic page breaks. Takes the pdf-lib module so it can be lazy-loaded.
async function buildLetterPdfBytes(PDFLib, text, subject) {
  const { PDFDocument, StandardFonts } = PDFLib;
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.TimesRoman);
  const bold = await doc.embedFont(StandardFonts.TimesRomanBold);
  const W = 595.28;
  const H = 841.89; // A4
  const margin = 56;
  const size = 11;
  const lh = 16;
  const maxW = W - margin * 2;
  let page = doc.addPage([W, H]);
  let y = H - margin;

  // Map the few common non-WinAnsi chars to safe equivalents (defensive — the
  // backend text is already WinAnsi-clean).
  const clean = (s) => s.replace(/ /g, ' ').replace(/‑/g, '-');
  const measure = (s, f = font) => f.widthOfTextAtSize(s, size);
  const space = () => {
    if (y - lh < margin) {
      page = doc.addPage([W, H]);
      y = H - margin;
    }
  };
  const wrap = (str, f, firstW, restW = firstW) => {
    const words = clean(str).split(/\s+/).filter(Boolean);
    const lines = [];
    let cur = [];
    for (const w of words) {
      const trial = [...cur, w].join(' ');
      const budget = lines.length === 0 ? firstW : restW;
      if (cur.length === 0 || measure(trial, f) <= budget) cur.push(w);
      else {
        lines.push(cur.join(' '));
        cur = [w];
      }
    }
    if (cur.length) lines.push(cur.join(' '));
    return lines.length ? lines : [''];
  };
  const drawLine = (str, x, f) => {
    space();
    page.drawText(str, { x, y, size, font: f });
    y -= lh;
  };

  // Subject (Betreff) — bold.
  for (const ln of wrap(subject, bold, maxW)) drawLine(ln, margin, bold);
  y -= lh * 0.6;

  for (const raw of text.split('\n')) {
    if (!raw.trim()) {
      y -= lh * 0.55; // blank line -> paragraph gap
      continue;
    }
    const label = BOLD_LABELS.find((l) => raw.startsWith(l));
    if (label) {
      const lw = measure(`${label} `, bold);
      const vlines = wrap(raw.slice(label.length), font, maxW - lw, maxW);
      space();
      page.drawText(label, { x: margin, y, size, font: bold });
      page.drawText(clean(vlines[0]), { x: margin + lw, y, size, font });
      y -= lh;
      for (let i = 1; i < vlines.length; i++) drawLine(vlines[i], margin, font);
      continue;
    }
    if (BULLET_RE.test(raw)) {
      const marker = '•  ';
      const mw = measure(marker, font);
      const blines = wrap(raw.replace(BULLET_RE, ''), font, maxW - mw);
      space();
      page.drawText(marker + blines[0], { x: margin, y, size, font });
      y -= lh;
      for (let i = 1; i < blines.length; i++) drawLine(blines[i], margin + mw, font);
      continue;
    }
    for (const ln of wrap(raw, font, maxW)) drawLine(ln, margin, font);
  }
  return doc.save();
}

// Editable declaration rendered as a document: a serif preview (header grid +
// prose + bulleted ongoing seizures, with source underlines) that flips to a
// mono textarea for edits, a mail-subject strip, and Copy / Download(.txt).
export default function DeclarationEditor({ declaration, caseRef, onToast, working, flash }) {
  const [text, setText] = useState(declaration?.text || '');
  const [editing, setEditing] = useState(false);
  // Manual mode regenerates the document on decision edits; if the operator
  // had ALSO hand-edited the text, that edit is replaced — say so once. The
  // notice is driven by a real "dirty" flag, not a text-diff heuristic, so it
  // no longer false-fires across cases or misses a revert-to-identical
  // recompose (audit B17). Home keys this component per case, so a genuinely
  // new case remounts fresh; only same-case recomposes reach the effect below.
  const [regenNotice, setRegenNotice] = useState(false);
  const [dirty, setDirty] = useState(false);
  const baseline = useRef(declaration?.text || ''); // last composed text shown

  useEffect(() => {
    const incoming = declaration?.text || '';
    if (incoming !== baseline.current) {
      // The composed document changed under us (a recompose / manual-off
      // revert). Warn only if the operator had unsaved hand-edits.
      if (dirty && text !== incoming) setRegenNotice(true);
      baseline.current = incoming;
      setText(incoming);
      setDirty(false);
      // Preserve the operator's Preview/Edit choice — do NOT force preview.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [declaration]);

  if (!declaration) return null;

  const composedBy = declaration.composed_by;
  const isLLM = composedBy && composedBy.startsWith('llm:');
  const isT2 = declaration.template === 'T2';
  const kind = declaration.kind || 'letter';
  const heroTitle =
    kind === 'email' ? 'Email' : kind === 'guidance' ? 'Operator guidance' : 'Declaration';

  const subject =
    declaration.subject || (text.split('\n').find((l) => l.trim()) || '').trim();

  const copy = async () => {
    try {
      if (navigator.clipboard.write && window.ClipboardItem) {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/html': new Blob([toRichHtml(text)], { type: 'text/html' }),
            'text/plain': new Blob([text], { type: 'text/plain' }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(text);
      }
      onToast && onToast('Copied to clipboard');
    } catch {
      try {
        await navigator.clipboard.writeText(text);
        onToast && onToast('Copied to clipboard (plain text)');
      } catch {
        onToast && onToast('Copy failed');
      }
    }
  };

  const copySubject = async () => {
    try {
      await navigator.clipboard.writeText(subject);
      onToast && onToast('Subject copied');
    } catch {
      onToast && onToast('Copy failed');
    }
  };

  // Selecting text in the read-only preview/subject and hitting ⌘C would let the
  // browser serialize the dark-theme text color into the clipboard HTML. Re-emit
  // the selection as clean HTML so it inherits the destination's text color.
  const handleSelectionCopy = (e) => {
    const selected = (window.getSelection && window.getSelection().toString()) || '';
    if (!selected || !e.clipboardData) return;
    e.preventDefault();
    e.clipboardData.setData('text/html', toRichHtml(selected));
    e.clipboardData.setData('text/plain', selected);
  };

  const download = () => {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    triggerDownload(blob, `drittschuldnererklaerung_${slugify(caseRef)}.txt`);
  };

  // Save the letter as a PDF. pdf-lib is loaded on demand (its own chunk) so it
  // never weighs down the initial load. The PDF is produced entirely in-app and
  // downloaded as a blob — no OS print/save dialog, which the Tauri webview
  // doesn't support.
  const savePdf = async () => {
    try {
      const PDFLib = await import('pdf-lib');
      const bytes = await buildLetterPdfBytes(PDFLib, text, subject);
      const blob = new Blob([bytes], { type: 'application/pdf' });
      triggerDownload(blob, `drittschuldnererklaerung_${slugify(caseRef)}.pdf`);
      onToast && onToast('Saved PDF');
    } catch {
      onToast && onToast('PDF export failed');
    }
  };

  // Split the letter into its header rows (bold-label lines) and the body.
  const headerRows = [];
  const bodyLines = [];
  for (const line of text.split('\n')) {
    const label = BOLD_LABELS.find((l) => line.startsWith(l));
    if (label) headerRows.push({ label, value: line.slice(label.length).trim() });
    else bodyLines.push(line);
  }

  // Body -> paragraphs, with ongoing-seizure bullet runs as a list and the
  // BO-sourced amounts underlined pink.
  const body = [];
  let i = 0;
  let k = 0;
  while (i < bodyLines.length) {
    const line = bodyLines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (BULLET_RE.test(line)) {
      const items = [];
      while (i < bodyLines.length && BULLET_RE.test(bodyLines[i])) {
        items.push(
          <li key={i}>
            <span className="src-bo">{bodyLines[i].replace(BULLET_RE, '')}</span>
          </li>
        );
        i++;
      }
      body.push(<ul key={`u${k++}`}>{items}</ul>);
      continue;
    }
    const hv = line.match(SEIZED_RE);
    if (hv) {
      body.push(
        <p key={`p${k++}`}>
          {hv[1]}
          <span className="src-bo">{hv[2]}</span>
          {hv[3]}
        </p>
      );
    } else {
      body.push(<p key={`p${k++}`}>{line}</p>);
    }
    i++;
  }

  return (
    <div className="hero" style={{ position: 'relative' }}>
      <WorkBar active={!!working} />
      <div className="hero-head">
        <span className="hero-title">{heroTitle}</span>
        <span className={`badge ${kind !== 'letter' ? 'accent' : isT2 ? 't2' : 't1'}`}>
          {declaration.template}
        </span>
        <span className={`badge ${isLLM ? 'accent' : ''}`}>
          {COMPOSED_LABEL[composedBy] || composedBy}
        </span>
        <span className="spacer" />
        {working && (
          <span className="badge" style={{ color: '#ec4899', borderColor: '#5a3246' }}>
            ⟳ {working}
          </span>
        )}
        {!working && flash && (
          <span className="badge" style={{ color: '#5ac88c', borderColor: '#325a46' }}>
            ✓ updated
          </span>
        )}
        <div className="seg">
          <button className={!editing ? 'on' : ''} onClick={() => setEditing(false)}>
            Preview
          </button>
          <button className={editing ? 'on' : ''} onClick={() => setEditing(true)}>
            Edit
          </button>
        </div>
      </div>

      {regenNotice && (
        <div className="banner warn" style={{ margin: '8px 0' }}>
          Document regenerated from the decision set — your manual text edits
          were replaced.{' '}
          <button className="btn small" onClick={() => setRegenNotice(false)}>
            OK
          </button>
        </div>
      )}

      <div className="subject-strip">
        <span className="lbl">Subject</span>
        <span
          className="val"
          title="First line — the mail subject"
          onCopy={handleSelectionCopy}
        >
          {subject}
        </span>
        <button className="btn small" onClick={copySubject}>
          Copy
        </button>
      </div>

      {editing ? (
        <div className="decl-edit"
             style={{ opacity: working ? 0.45 : 1, transition: 'opacity .25s ease' }}>
          <textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setDirty(true);
            }}
            spellCheck={false}
            autoFocus
          />
        </div>
      ) : (
        <>
          <div
            className="letter"
            onDoubleClick={() => setEditing(true)}
            onCopy={handleSelectionCopy}
            title="Double-click to edit"
            style={{ opacity: working ? 0.45 : 1, transition: 'opacity .25s ease' }}
          >
            {headerRows.length > 0 && (
              <div className="letter-head">
                {headerRows.map((r, idx) => (
                  <React.Fragment key={idx}>
                    <span className="lh-label">{r.label}</span>
                    <span>
                      <span className="src-jira">{r.value}</span>
                    </span>
                  </React.Fragment>
                ))}
              </div>
            )}
            {body}
          </div>
          <div className="src-legend">
            <span>
              <span className="sw jira" />from Jira ticket
            </span>
            <span>
              <span className="sw bo" />from Back-Office
            </span>
          </div>
        </>
      )}

      <div className="hero-actions">
        <button className="btn primary" onClick={copy}>
          Copy
        </button>
        <button className="btn" onClick={savePdf}>
          Save as PDF
        </button>
        <button className="btn" onClick={download}>
          Download .txt
        </button>
      </div>
    </div>
  );
}
