# BO endpoint discovery — read-only survey

*2026-07-30 · sources: live BO probes (`bo_get`/`bo_openapi` via the finom-bo MCP) + `backend-rag`
code search. No writes were performed anywhere.*

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

## Recommended next candidates for bigi (all read-only)

1. **RFI answer pack** — for RFI tickets: transactions for the requested period
   (`inhouse/v2/company-transactions`) + statement export + CDD profile/export, assembled
   into the operator's response bundle. Directly answers the requests list seen in live
   RFI tickets (transactions, balances, account opening documents, other seizures).
2. **Seizure history context** — legacy + TM seizure data merged for the case view.
3. **TM repo access** — request GitLab access to `M69/back/transactionmonitoring` to confirm
   seizure create/transition contracts and alert models at the source.
