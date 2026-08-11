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

## 4. False PERSON_VS_COMPANY for company debtors with a DOB on the ticket — ✅ IMPLEMENTED

**Case:** FPOPCL-31103 (Magcars UG (haftungsbeschränkt), `d7fa3c9c-…`) → S5/T9
instead of S1/T1.

**Root cause:** `is_physical_person` = "DOB present AND no register number"
(spec Q10a heuristic). Porters filled the LR's date of birth on a COMPANY
debtor ticket, and the register-number field was empty → misclassified as a
person → S5 override fired on the matched Company account.

**Fix (implemented):** `app/matching.py::is_physical_person` returns False when
the debtor NAME carries a company legal form (reuse `_LEGAL_SUFFIX_RE` — UG
(haftungsbeschränkt), GmbH, GmbH & Co. KG, AG, KG, GbR, e.K., …), regardless
of DOB. A company cannot have a birthday; the name is the stronger signal.

**Expected after fix (FPOPCL-31103):** MATCH (IBAN + strong address) without
the person override → S1/T1, own-case amount declared. Regression tests:
company-name+DOB → not a person; plain person name + DOB → still a person;
person name + register number → not a person (unchanged).

## 5. Seizure-link UUIDs in comments pollute the company candidates — ✅ IMPLEMENTED

**Case:** FPOPCL-31102 (Susann Piekorz). Porters now post TWO comments:
`### Customer Matching / Definitive matches: 27e657bd-…` and
`Backoffice URL to the created seizure: https://inhouse.finom.co/monitoring/seizures/fb31301a-…/transactions`.
bigi harvested BOTH UUIDs → 2 "company" candidates → needless picker stop.
BO confirms `fb31301a-…` 404s as a company (it is the seizure entity's ID).

**Fix (implemented):** three layers in `app/jira.py` + `app/matching.py`:
1. Strip URLs containing `/seizures/` from comment text BEFORE harvesting —
   those UUIDs are seizure IDs, never companies (optionally keep as an
   own-seizure link note in the trace).
2. Tiered merge across comments: definitive > potential > bare — a labeled
   "Definitive matches:" UUID in ANY comment wins outright; bare UUIDs from
   other comments only matter when no labeled ones exist. (Today
   `extract_match_uuids` flattens tiers per comment and
   `fetch_comment_match_uuids` merges flatly, newest comment first — the
   seizure link is the NEWER comment, so the bogus UUID even sorts first.)
3. Safety net: ticket-UUID candidates whose BO lookup says company-not-found
   are dropped (with a note); exactly one valid candidate left → auto-resolve.

## 6. DOB confirmation fails on date-format differences — ✅ IMPLEMENTED

**Case:** same ticket. Account CDD carries the matching birthdate — the
`PersonBirthdate` node has empty `values` but `properties: [{name: "Date of
Birth", value: "21.08.1989"}]` (subtree harvest picks it up) — yet the ticket
says `1989-08-21` and `matching.py` line ~676 compares normalized STRINGS:
`_norm("1989-08-21") != _norm("21.08.1989")` → Freelancer DOB rule failed →
identified-but-unconfirmed → S4/T7 "ask for IBAN" even after the operator
picked the right account.

**Fix (implemented):** compare `iso_date_any(ticket_dob) == iso_date_any(dob)`
(both formats already supported there), falling back to the current string
equality when either side does not parse as a date.

**Bonus finding while implementing:** the label regex only accepted the
singular "definitive match" — Porters' actual comment says "Definitive
match**es**:", so the definitive tier never applied on 31102. Fixed
(`match(?:es)?`), and the description parser now accepts the plural field
keys too.

**Expected after #5+#6 (FPOPCL-31102):** definitive comment UUID resolves
Susann Piekorz directly (no picker), Freelancer confirms by DOB despite the
postcode difference (02997 Wittichenau vs 02977 Hoyerswerda — address OR DOB),
account OPEN, own case 4103-K-PK-ZuZ/99000000024482889 with the €138,03
Seizure wallet → **S1/T1**, no manual steps.

## 7. Align confirmation with the analyst identification matrix — ✅ IMPLEMENTED

**Source:** analyst team's accepted-identification rules (2026-08). Collapsed
(supersets removed): **Company** = name + (address | IBAN) -> definitive.
**Freelancer** = name + (address | DOB | IBAN) -> definitive; the register /
trade name may differ slightly from the main name and still count.

**Gap:** bigi's confirmation never checks the NAME — it is only the implicit
search key. In the ticket-UUID / comment-UUID / manual paths an IBAN or
address match confirms even when the debtor name disagrees with the account.
Per the matrix, name agreement is a required component of EVERY definitive
match (IBAN alone is not definitive).

**Fix (implemented):**
1. Add a name gate to confirmation in `app/matching.py`: ticket debtor name
   vs account businessName OR the CDD registered/trade name, compared with
   legal-suffix-stripped normalization (reuse `name_variants`/`_same_name`),
   fuzzy-tolerant like the graded address check.
2. Name agrees + (IBAN | address[strong] | DOB-for-freelancer) -> MATCH
   (unchanged signals, now gated). Name DISAGREES + IBAN match -> not
   definitive: surface as identified-not-confirmed (operator), with an
   explicit "IBAN matches but name differs" reason.
3. Notes: legal-form conflict (e.g. UG vs GmbH explicit on both sides) is a
   warning note; tax ID is never sufficient alone (no code path needed).

**Dependency:** item 6 (DOB format normalization) is REQUIRED by the matrix's
"Freelancer name, DOB match -> definitive" rule.

## Reference — how to capture new findings

`\.venv/bin/python3 scripts/case_debug.py FPOPCL-XXXXX [--company <uuid>] [--no-match]`
against a RESTARTED uvicorn (`--reload` recommended while triaging).
