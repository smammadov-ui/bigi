# Troubleshooting backlog — confirmed findings awaiting "go ahead"

*Collected during live case triage; items are implemented in batches on
explicit go-ahead, with regression tests per item.*

## 1. `same_case` middle-segment collision → competing seizure missing from T2 ⚠️ legal impact — ✅ IMPLEMENTED

**Case:** FPOPCL-31227 (Simons GmbH & Co. KG, `52603609-a984-…`).

**Symptom:** BO shows 4 Processing seizures; the T2 letter lists only 2
bullets. `ignored_same_case: 2` — the June AOK seizure was wrongly classified
as this ticket's own case.

**Evidence (verified computationally):** ticket `case_references` =
`82611302`; June AOK row's caseNumber = `document_id: 61c5d30b-… |
case_reference: V00001726483-82611302-57223`. Normalized containment matches
the ticket ref against the MIDDLE segment — `82611302` is AOK's
customer/member number for this debtor, shared across ALL AOK cases. June's
claim is a different case (36.017,92 vs the ticket's 1.232,13) and belongs in
the letter as a competing seizure.

**Fix (agreed, pending go):** tighten `app/checks.py::same_case`:
- normalized equality -> match (unchanged; covers formatting variants);
- containment only at reference EDGES on segment boundaries — the shorter
  ref's full segment sequence must be the PREFIX or SUFFIX of the longer one
  (keeps: court tail `12619/26 F` ⊂ `2614/… - VO 05 - 12619/26 F`; full
  `case_reference:` value ⊂ Porters `document_id: … | case_reference: …`);
- middle-segment hits (`82611302` inside `V…-82611302-57223`) -> NO match;
- collapsed-string prefix/suffix fallback only for refs ≥ 10 normalized chars.

**Safety nets to add with it:**
- pipeline warning whenever >1 BO rows classify as own-case;
- debug trace (`app/trace.py`) lists the ignored/competing caseNumbers.

**Expected after fix (FPOPCL-31227):** own = today's row only; three bullets —
AOK 36.017,92 · Hauptzollamt Gießen 1.116,95 · Kreisausschuss D157013
91.546,30. Regression tests: middle-segment non-match, tail/prefix still
match, Porters combo still matches, own-case-duplicate warning.

## 2. Open ops question — guide case 3 (open MNL-20-FP, civil ticket)

FPOPCL-30930 resolves S2/T2 today: per spec, an open MNL-20 on a civil
new-style case means "proceed with the seizure flow" (the alert is the work
queue, not a blocker). The June guide's case-3 example (24619) was a criminal
warrant, which routes out separately. **Needs ops answer:** for a CIVIL ticket
with an open MNL-20 alert, is the TPD letter expected (current behavior) or
manual handling?

## 3. Open ops question — `LimitedAccount` WITHOUT seizure activity

No rule exists in the ops guide/SOP. Current (conservative) behavior: routes
to the operator. With verified seizure activity it proceeds (ruled by
FPOPCL-31056 being filed under guide case 2). If ops declares limited accounts
ordinary for seizure purposes, move `LimitedAccount` from RESTRICTED to OPEN
(fresh first seizure -> S1/T1 without a human detour).

## Reference — how to capture new findings

`\.venv/bin/python3 scripts/case_debug.py FPOPCL-XXXXX [--company <uuid>] [--no-match]`
against a RESTARTED uvicorn (`--reload` recommended while triaging).
