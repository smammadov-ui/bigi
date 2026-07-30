# bigi — Third-Party Declaration (Drittschuldnererklärung), all 11 scenarios

**bigi = mini's app shell + the full feizure scenario engine, strictly READ-ONLY toward Back-Office.**

Paste (or fetch from Jira) a German bank-account-seizure ticket. bigi parses it
deterministically, identifies and **confirms** the account in Finom Back-Office,
checks alerts, ongoing seizures, account status, and balance, resolves one of the
**11 coded scenarios**, and composes the corresponding German document — a §840
ZPO declaration letter or an email — ready to copy, edit, or export as PDF.

bigi **never writes to BO**: it does not create, execute, or modify seizures.
By the time a TPD is generated, this ticket's seizure is expected to already
exist in BO — bigi recognises it as the case's **own seizure** (its
`seizedAmount` is the authoritative declared figure) and warns when it is
missing.

## The 11 scenarios

| Scenario | Trigger | Template | Output |
|---|---|---|---|
| S1 | Match, no open alerts, no competing Processing seizure | T1 | §840 letter |
| S2 | Match, ≥1 competing Processing seizure | T2 (+ one bullet per seizure) | §840 letter |
| S3 | Account closed before the ticket / onboarding | T6 | §840 letter (Kundenbeziehung: Nein) |
| S4_NO_IBAN | No match, no IBAN provided | T7 | email (ask for IBAN) |
| S4_IBAN | No match, IBAN provided but unknown | T8 | email (ask for correct IBAN) |
| S5 | Request against a person, account is a Company | T9 | email (attach the received doc) |
| S6A | Closing + covered (Processing seizure or zero balance) | T10 | email |
| S6B | Closing + balance left, no Processing seizure | T11 | email (Restbetrag; transfer handled in BO) |
| INSOLVENCY | Open MNL21 alert | T4 | email |
| RFI | Open MNL22 alert (information request) | T5 | operator guidance (no seizure, no letter) |
| ROUTED_OUT | Criminal warrant / Repeal (cancellation) / Restriction (reduction) / compliance-blocked / closed-after-ticket / other open alert / undecidable on degraded data | — | operator, no document (with specific guidance notes) |

Resolution order: **open alerts → match outcome → status bucket → seizures/balance.**

## Correctness rules carried over from mini (applied to all scenarios)

- **Own-case filtering** — a Processing seizure whose `caseNumber` matches the
  ticket's case reference is this ticket's own case: it never flips S1→S2,
  never contaminates S6A's covered test, and its BO `seizedAmount` is the
  declared "in Höhe von" figure.
- **Junior filtering** — competing seizures created after this case are not declared.
- **Structured T2 bullets** — built from BO's structured fields (issuedBy /
  amount / issuedOn), not the free-text comment; Jira links stripped.
- **EUR-only balance** — no FX guessing in a legal figure; non-EUR wallets are
  reported for the operator. Wallet balances are read with
  debt/on-hold-excluding flags; funds held under seizures are shown from the
  seizure records.
- **Degradation policy** — BO read failures never crash a case: each failed
  check is surfaced (`error`/`assumed`) and undecidable CLOSING cases route to
  the operator instead of guessing a legal figure.
- **Never guess an account** — ambiguous identification surfaces candidates for
  operator selection (`pending_selection`); confirmation (IBAN → Company
  address → Freelancer address/DOB) keeps lookalike hits from becoming a
  wrong-debtor declaration.

## Run it

Dev (two processes):

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate \
  && pip install -r requirements.txt && uvicorn app.main:app --reload   # :8000
cd frontend && npm install && npm run dev                               # :5173 (proxies to :8000)
```

Docker (single container, SPA served by the backend):

```bash
docker build -t bigi . && docker run --rm -p 8000:8000 -v bigi_data:/data bigi
# open http://localhost:8000
```

Then configure credentials, either way (the Settings UI wins when both are set):

- **Settings UI**: BO base URL + INTTOKEN (read-only endpoints only), optionally
  an LLM key (OpenAI/Anthropic; without one, documents are filled
  deterministically) and Jira credentials (fetch/browse). Each section has a
  *Test connection* probe. Values are stored in the local SQLite DB.
- **Environment / `.env` file** (keeps tokens out of the UI, chat, and git):
  copy `.env.example` to `backend/.env` (gitignored) and fill `BO_BASE_URL`,
  `BO_INTTOKEN`, `LLM_API_KEY`, … — or pass them to docker:
  `docker run -e BO_BASE_URL=… -e BO_INTTOKEN=… …`. Env values act as
  fallbacks whenever the corresponding Settings-UI field is unset; the UI
  shows *"from environment"* for them.

Secrets are always masked (`••••1234`) toward the browser, whatever the source.

## Ingestion

- Paste the ticket → **Generate**
- `POST /api/declaration` `{raw_text, company_uuid?}`
- Jira: fetch by key/link (`POST /api/jira/fetch`), browse by JQL
  (`GET /api/jira/search`), webhook (`POST /api/webhook/jira`, optional
  `JIRA_WEBHOOK_SECRET`)

Result `status`: `ok` (scenario + document), `pending_selection` (operator must
pick a candidate account / enter a UUID; the UI re-runs with it), `halted`
(invalid ticket data — masked IBAN, several debtor IBANs, unparseable amount —
no BO call is made).

## BO endpoints used (all read-only)

`cstools_search`, `cstools_short_info`, `cstools_overview`, `cdd_profile`,
`wallets` (debt/on-hold-excluding flags), `get_alerts`, `list_seizures`
(paginated — competing seizures beyond page 1 are never lost), `get_seizure`,
`whoami`. The INTTOKEN travels only as a `Cookie: INTTOKEN=…` header and is
never logged. There is deliberately no business-data write in the client.

**Workspaces (FP + PNL):** per the ops SOP, debtors must be searched in BOTH
workspaces. The workspace is server-side user state, so for the duration of
one case bigi widens the active selection to all available workspaces
(`POST /api/cstools/user-context/set` — a session preference, the single
non-read call) and restores the original selection afterwards; any failure
degrades to single-workspace with a visible warning.

## Tests

```bash
cd backend && python -m pytest -q     # 250 tests, offline (StubBO)
```

`tests/test_scenarios.py` drives every scenario end-to-end through
`run_pipeline`; the rest cover parser, matching/confirmation, checks, amounts,
templates T1–T11, the guarded LLM composer, settings, webhook, and Jira.

## Known gaps / open spec questions

- **S5 detection heuristic** (spec Q10a): "physical person" = DOB present and
  no register number; the freelancer-account cross-lookup is not implemented.
- **T11 `[Case number]`** (spec Q11): rendered from the Jira case reference —
  the BO case-number field is still unconfirmed.
- T5 (RFI) renders operator guidance, not a customer letter, by design.
- No recipient directory: the operator addresses the email/letter themselves.
- Security posture matches mini (local/demo): plaintext secrets in the local
  SQLite settings table, CORS `*`, webhook open unless a secret is set.
