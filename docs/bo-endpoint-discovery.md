# BO endpoint discovery — read-only survey

*2026-07-30 · sources: live BO probes (`bo_get`/`bo_openapi` via the finom-bo MCP) + `backend-rag`
+ `frontend-rag` (**portal** repo — the BO UI) code search. No writes were performed anywhere.*

## Access situation

- The BO gateway (`inhouse.finom.co`) exposes **no Swagger/OpenAPI** (404 on the usual
  `/api/{service}/swagger/v1/swagger.json` paths) — the backend code is the endpoint catalog.
- `backend-rag` honours GitLab ACLs. Accessible repos: `M69/back/bank`, `M69/back/cstools`,
  `M69/back/customerdossier`, `M69/back/finom`, `M69/back/thepipe`.
- **Not accessible: `M69/back/transactionmonitoring`** — the service behind bigi's seizure +
  alert endpoints. Its create/transition contracts and alert models are only visible through
  its consumers. Getting GitLab access to this repo would close the last blind spot.
- Route paths below are as written in code (`[Route("inhouse/…")]`); on the gateway they are
  served under `/api/{service}/…`. Exact gateway prefixes should be confirmed with one live
  `bo_get` probe per endpoint before use.

## What bigi uses today (baseline)

| Endpoint | Service | Used for |
|---|---|---|
| `POST /api/cstools/v2/companies` | cstools | search by IBAN / name / register number |
| `GET /api/cstools/companies/{id}/short-info` | cstools | account/payment status |
| `GET /api/cstools/companies/{id}/overview` | cstools | type, address, legal form |
| `GET /api/customerdossier/companies/{id}/cdd-profile` | customerdossier | PersonBirthdate (Freelancer rule) |
| `GET /api/bank/wallets/?companyId=…` | bank | balances (debt/on-hold netted) |
| `POST /api/transactionmonitoring/companies/{id}/alerts` | TM | open-alert rules (MNL20/21/22) |
| `POST /api/transactionmonitoring/company/seizures` | TM | seizure listing (paginated) |
| `GET /api/transactionmonitoring/seizure/{id}` | TM | seizure detail (comment, amounts, clientTotal) |

## Additional READS discovered

### cstools
- `POST inhouse/v2/company-transactions` (+ v1, + `…/total`) — full **transaction lists per
  company**. Key for RFIs ("Kontoverdichtung für Zeitraum X–Y"). Carries
  `SensitiveDataRequestAudit` (see compliance notes).
- `GET inhouse/scheduledpayments/bycompany/{companyId}` — scheduled payments.
- DocumentsV2: document download handlers (`inhouse/…/documents/{docId}/download`).
- `GET inhouse/company/{companyId}/enhancedmonitoring` — monitoring status.
- `GET inhouse/four-eye-check-requests/{id}` — inspect a four-eyes request.

### bank
- **Statements**: create statement for selected wallets + period
  (`CreateStatementRequestHandler`, max-days validation), **CSV export** incl. multibank
  accounts (`ExportCsvRequestHandler`) — RFI "Kontoauszüge".
- `GET inhouse/wallet/{walletId}/averagebalance` — average balance export.
- `GET inhouse/wallets/{id}/pdf/dwn` — wallet requisites (RIB) PDF.
- `GET inhouse/company/{companyId}/seizures` — legacy per-company seizures
  (`[Obsolete]` → TM dashboard is the successor; bigi already uses TM).

### customerdossier
- `GET inhouse/companies/{id}/cdd-profile` — in use.
- `GET inhouse/companies/{id}/cdd-profile/actions` — available dossier actions.
- `GET inhouse/companies/{id}/dossier-questionnaire` — dossier questionnaire.
- **CDD export**: `POST inhouse/cdd-profile/export` +
  `GET inhouse/documents/download/export/{companyId}/{date}` — the "account opening documents /
  Personalien" package RFIs ask for.

### thepipe (analytics)
- Seizures stream to Kafka (`SeizureKafkaModel`: `AmountEur`, `FourEyeCheckUserEmail`,
  `CreatedByEmail`, …) — a ready-made feed for seizure dashboards/reporting.

## ACTIONS that exist (awareness only — bigi stays read-only)

- **Seizure approve/decline** via the four-eyes framework: `SeizureApproveProcessor`
  (cstools `BusinessLogic/Seizures`) resolves a `FourEyeCheckRequest` and forwards via
  ServiceMesh into TM; role-gated, Slack-notified.
- **Seizure create / transitions** — in the TM service (repo not in ACL; known from the root
  feizure spec as `tm_create_seizure` + status transitions).
- **Manual transfers** (`CreateManualTransferRequestHandler` + resolution processors) — the
  old-style seizure money movement (main → seizure → external), four-eyes + Slack.
- Enhanced/fraud monitoring toggles (`inhouse/fraudmonitoring/{userId}/disable`, …).
- Account decline (customerdossier `AccountDeclineService`).

## Compliance-relevant patterns

1. **Sensitive reads are audited**: transaction-list requests carry
   `SensitiveDataRequestAudit` → every call writes an `InhouseAuditEvent` attributed to the
   token owner. If bigi starts pulling transactions for RFIs, each request is logged against
   the INTTOKEN's user.
2. **Granular permissions**: every handler is gated by `Permissions.backOffice.*` — the
   reachable surface depends on the roles behind the token. Probe with `bo_get` before
   building on an endpoint.

## The portal (BO frontend) — definitive TM seizure API surface

`M69/back/backoffice` is an empty stub repo ("init"); the actual portal is the **`portal`**
frontend repo (indexed by frontend-rag). Its `apps/monitoring/service/seizure.ts` is the UI's
client for the entire TransactionMonitoring seizure feature — the authoritative endpoint list
even though the TM backend repo is not in our GitLab ACL:

**Reads**
- `POST /api/transactionmonitoring/seizures` — **global seizure dashboard** (all companies;
  filters: prompt, statuses, assignees, countries, raisedOn/resolvedOn/dueDate ranges; sort).
- `POST /api/transactionmonitoring/company/seizures` — per company *(bigi uses)*.
- `GET /api/transactionmonitoring/seizures/filters` + `POST …/company/seizures/filters`.
- `GET /api/transactionmonitoring/seizure/{id}` — detail *(bigi uses)*.
- `GET /api/transactionmonitoring/seizure/{id}/wait/{version}` — long-poll for live updates.

**Writes** (awareness only)
- `POST /api/transactionmonitoring/seizure` — create; `PUT …/seizure` — update (IBAN/BIC).
- `POST …/seizure/process` — initiate seizure transfer; `POST …/seizure/transfer/external` —
  external transfer (amount); `POST …/seizure/refund`; `POST …/seizure/archive` (comment).
- `POST …/seizures/note` + `PUT …/files/upload` / `POST …/seizures/notes/file` — notes/files.

**Full `SeizureStatus` enum (18 values — richer than the spec's 10):**
`Undefined, Created, PendingApproval, Rejected, Approved, Processing, PendingSeizure, Seized,
PendingTransferApproval, TransferRejected, TransferApproved, PendingTransfer, Transferred,
Resolved, PendingRefund, Refunded, Archived, Failed, TransferFailed`

> **Open question for ops (spec Q7 revisited):** bigi counts only `Processing` as a competing
> "bestehende Pfändung" (per the original Q7b answer). The full lifecycle shows pre-Processing
> states (`Created`, `PendingApproval`, `Approved`, `PendingSeizure`) and captured states
> (`Seized`, `PendingTransferApproval`, `PendingTransfer`) that arguably also represent
> existing seizures a §840 declaration must mention. Needs an ops ruling before changing.

**Notable detail fields** (`SeizureRetrieveResponseBody`): `deliveredOn` (BO's own
Zustellungsdatum — cross-checkable against the ticket's date received), `dueDate` (deadline
tracking for the §840 response window), `decision`, `processCount`, `fullTransfer`,
`isDutchFiu`, `walletsIds`, `fourEyeCheckId`, plus the balances already used
(`seizedAmount`, `allWalletsBalance`, `euroWalletsBalance`).

Also confirmed accessible along the way: `M69/back/aiassistant` (accounting threads) and
`M69/credits/creditcore` (per-company credit info via its own Backoffice API).

## Recommended next candidates for bigi (all read-only)

1. **RFI answer pack** — for RFI tickets: transactions for the requested period
   (`inhouse/v2/company-transactions`) + statement export + CDD profile/export, assembled
   into the operator's response bundle. Directly answers the requests list seen in live
   RFI tickets (transactions, balances, account opening documents, other seizures).
2. **Seizure history context** — legacy + TM seizure data merged for the case view.
3. **TM repo access** — request GitLab access to `M69/back/transactionmonitoring` to confirm
   seizure create/transition contracts and alert models at the source (the portal client now
   covers the seizure endpoints; alert models remain unseen).
4. **Deadline awareness** — surface the seizure's `dueDate` and BO's `deliveredOn` in bigi's
   case view (cross-check against the ticket's date received).
5. **Ops ruling on the status enum** — decide which of the 18 statuses count as competing
   seizures (see the open question above), then adjust `is_processing`/filtering if needed.
