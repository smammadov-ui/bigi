# Manual mode — design plan — ✅ IMPLEMENTED (branch `manual-mode`)

**v1 deviations from the plan (deliberate):**

- **Persistence deferred.** bigi has no case table (settings KV only), so the
  decision set + override diff are not stored server-side yet; the UI shows
  the override badge and the auto values live in `result.manual`. Add a case
  table if audit storage becomes a requirement.
- **Delivery is derived from the template** (letters are letters, emails are
  emails) instead of being an independent decision.
- **All manual controls live in the Decision panel** (including seizure roles
  and balance figures) instead of being spread across the existing rail
  panels — one clear place for everything the operator can change.
- **Halted tickets do not un-halt via field overrides** (the halt check runs
  in the parser); fix the ticket or paste corrected text instead.

**Requested:** 2026-08-11 — "Once parsed, they can choose template, choose
which seizures they want to report and etc."

**Decided (2026-08-11):** entry = toggle per case; audit = logged diff only
(no mandatory reason notes); v1 scope = template + seizure roles + amounts/
balance edits + wallet/IBAN + email slots + parsed-field editing. The
extra-note paragraph is deferred (future idea).

## Core idea: auto proposes, operator disposes

There is no separate "blank manual flow". The auto pipeline always runs and
its output becomes an editable **decision set**. Auto fills the defaults,
manual mode unlocks them, and the document is a pure function of the decision
set — recomposed live on every change. Overrides are visible and logged
against the auto recommendation.

Why this shape:

- Dead ends become completable: `pending_selection`, ROUTED_OUT, UNKNOWN
  status — the operator finishes the case in bigi instead of falling back to
  raw BO + Word.
- Wrong auto decisions become two clicks instead of a code-fix wait
  (FPOPCL-31227: a competing seizure dropped by matching → operator ticks it
  back; FPOPCL-31103: wrong template → operator picks T1).
- The checks stay: deviations produce visible warnings, never blocks.
- Audit: the stored case records auto recommendation vs operator override.

## The decision set (contract between pipeline, UI, composer)

```jsonc
{
  "template": "T2",                  // operator-selectable T1..T11 (auto's pick is the default)
  "delivery": "letter",              // letter | email (T7/T8 default to email)
  "recipient_email": "",             // for email templates
  "subject": "",                     // for email templates (auto-derived default)
  "seized_iban": {"value": "DE…", "source": "main_wallet"},  // wallet | ticket | manual
  "seizures": [                      // every BO row the pipeline saw
    {"id": 9, "case_ref": "…", "creditor": "AOK PLUS", "amount": 36017.92,
     "role": "report",               // own | report | ignore
     "auto_role": "ignore",          // what auto decided (for the audit diff)
     "note": "same_case matched / junior / …"}
  ],
  "own_case_amount": 138.03,
  "available_eur": 8609.02,
  "seizable_eur": 0.0,
}
// (an optional extra-note paragraph was considered and DEFERRED from v1)
```

Roles: **own** = our case (quoted as the own seizure), **report** = listed in
the declaration as a prior/competing seizure, **ignore** = junior/unrelated —
mentioned nowhere.

## UX

- **"Manual mode" toggle per case** (default off). Panels switch from display
  to edit:
  - New **Decision panel** (top of right column): template dropdown grouped by
    family (declarations T1–T5 / closed–closing T6, T10, T11 / emails T7–T8 /
    person T9), auto's recommendation badged "(auto)". Under it a per-template
    slot checklist: which data the chosen template needs and whether it is
    filled (T2 → ≥1 reported seizure; T7/T8 → recipient email; …).
  - **Seizures panel**: per-row role selector + amount field; auto's
    own-case/junior/same_case decisions preselected and annotated.
  - **Balance panel**: available/seizable figures editable.
  - **Account panel**: seized-IBAN wallet dropdown.
- Live recompose on every change (stateless backend call, no BO re-fetch).
- The existing text **Edit tab stays the last layer**. Structured changes
  regenerate the text — warn before discarding manual text edits.
- Non-blocking **yellow warnings** for contradictions:
  - T1 ("no other seizures") while unticked `report`-able rows exist
  - reported amounts vs prose totals don't add up
  - email template without a recipient email
  - T6/T10/T11 while the account is OPEN, T1–T5 while CLOSED
  - own case unassigned (no row has role `own`) on templates that quote it
- Dead-end states keep the Decision panel available — e.g. compose a T7 email
  with no account resolved at all (it needs almost nothing).

## Backend

- Refactor compose into a pure function: `compose(decision_set, case_context)
  → document`. No BO calls inside.
- `POST /api/declaration` additionally returns the auto-filled
  `decision_set`.
- New `POST /api/declaration/compose`: stateless recompose from a posted
  decision set (used by the live preview). Read-only stance unchanged.
- Persist `decision_set` + override diff on the stored case row; extend
  `build_trace` with `auto vs final` per overridden field.
- `case_debug.py` grows flags for testing: `--template T2`,
  `--report-seizures 9,12`, `--role 9=own`.

## Guardrails

- Warnings, never blocks — the operator is the authority (matches ops
  reality; the letter is reviewed before sending anyway).
- Every override logged: field, auto value, manual value, timestamp.
- UI badge on the output card: "manual overrides: template, seizures (2)" —
  in the UI only, never printed into the letter.
- BO write surface: still none.

## Phases (when approved)

1. **Backend**: decision-set extraction from the current pipeline, pure
   compose, `/compose` endpoint, persistence + trace, tests (compose every
   template from arbitrary decision sets; warning matrix).
2. **Frontend**: Decision panel, seizure role editing, balance/IBAN/email
   slots, live recompose, regenerate-vs-text-edit warning.
3. **Polish**: audit diff in trace, case_debug flags, README + ops-guide
   cross-reference.

## Parsed-field editing (in v1 per decision)

A separate edit layer BEFORE the pipeline: after parse, the operator can
correct parser mistakes (amounts, dates, addresses, case references, debtor
name/IBAN) and re-run. Distinct from the decision set: parsed-field edits
re-run identification + checks (BO re-fetch), decision-set edits only
recompose. UI: "Edit parsed fields" expander under the warnings banner;
edited fields marked against the raw parse in the trace.

## Final decisions (2026-08-12) — plan complete, ready to build

1. **Cross-hints: passive.** When the operator's data implies a different
   template, show a warning text that names the fitting template ("your
   selection matches T1 — no other seizures reported"); never auto-switch or
   nag.
2. **Scenario label: show both.** When the template is overridden the badge
   reads "T1 — manual (auto: S2/T2)"; the stored case and trace keep both
   values.
