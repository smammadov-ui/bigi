"""Finom Back-Office client — the READ-ONLY slice bigi needs (httpx + INTTOKEN).

Base is mini's ``bo_client.py`` (paginated ``list_seizures`` so competing
seizures beyond page 1 are never silently lost), extended with the four
read endpoints the 11-scenario resolution requires: ``cstools_short_info``,
``cstools_overview``, ``cdd_profile``, ``get_alerts``.

bigi never writes: there is deliberately NO ``create_seizure`` here — the
ticket's own seizure is expected to already exist in BO by the time the TPD
is generated.

The INTTOKEN is sent ONLY as a ``Cookie: INTTOKEN=<token>`` header and is NEVER
logged. On any non-2xx response or transport error, raise :class:`BOError` whose
message carries the tool name, status code, and (truncated) response body — but
never the request auth header.
"""
from __future__ import annotations

import httpx

_BODY_LIMIT = 2000


class BOError(Exception):
    """Upstream Back-Office failure. Maps to HTTP 502 in routers."""

    code = 502

    def __init__(self, tool: str, status_code: int | None = None, body=None):
        self.tool = tool
        self.status_code = status_code
        self.body = body
        status = status_code if status_code is not None else "transport"
        super().__init__(f"BO {tool} failed ({status}): {body}")


def status_name(x) -> str:
    """Normalize a seizure status that may be a plain string OR ``{"name": ...}``."""
    if isinstance(x, str):
        return x
    return (x or {}).get("name", "") if isinstance(x, dict) else ""


def is_processing(seizure: dict) -> bool:
    """True iff the seizure's status name is exactly ``"Processing"``."""
    return status_name((seizure or {}).get("status")) == "Processing"


# Statuses of seizures that have already CAPTURED funds and are settling —
# waiting for the payout to the creditor to be approved/executed. The captured
# money still shows on the EUR wallets until the transfer runs, but it is
# spoken for: a new seizure gets nothing from it. Ops rule (FPOPCL-31278,
# Enbio UG): a CLOSING account whose balance is fully captured by such a
# seizure counts as zero balance -> S6A, not S6B.
SETTLING_STATUSES = frozenset({"PendingTransferApproval"})


def is_settling(seizure: dict) -> bool:
    """True iff the seizure holds captured funds pending transfer approval."""
    return status_name((seizure or {}).get("status")) in SETTLING_STATUSES


class BOClient:
    """Thin httpx wrapper over the Finom cstools / transaction-monitoring API.

    Methods are monkeypatch-friendly: tests patch individual methods on an
    instance (or pass a duck-typed stub), or patch ``httpx``.
    """

    def __init__(self, base_url: str, inttoken: str, timeout: float = 30.0):
        self.base_url = (base_url or "").rstrip("/")
        self.inttoken = inttoken or ""
        self.timeout = timeout

    # -- internals ---------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Cookie": f"INTTOKEN={self.inttoken}",
            # Without this, BO masks "sensitive" fields (wallet balance/debt/
            # onHold/totalOnProcessing) as 0.0 — the header unmasks them.
            "sensitive-data": "true",
        }

    def _require_base(self, tool: str) -> None:
        if not self.base_url:
            raise BOError(tool, None, "BO base URL not configured")

    def _post(self, tool: str, path: str, json_body: dict) -> dict:
        self._require_base(tool)
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.post(url, json=json_body, headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise BOError(tool, None, f"{type(exc).__name__}: {exc}") from exc
        return self._handle(tool, resp)

    def _get(self, tool: str, path: str) -> dict:
        self._require_base(tool)
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise BOError(tool, None, f"{type(exc).__name__}: {exc}") from exc
        return self._handle(tool, resp)

    @staticmethod
    def _handle(tool: str, resp: "httpx.Response") -> dict:
        if not (200 <= resp.status_code < 300):
            body = (resp.text or "")[:_BODY_LIMIT]
            raise BOError(tool, resp.status_code, body)
        try:
            return resp.json()
        except ValueError as exc:
            raise BOError(tool, resp.status_code, f"invalid JSON: {exc}") from exc

    # -- identification / account ------------------------------------------

    def cstools_search(self, text: str) -> dict:
        """Company search -> ``{"items": [...], ...}``.

        Uses ``POST /api/cstools/v3/companies`` — the portal's global-search
        ("boogle") endpoint: fuzzy, extracts strong identifiers (company GUID /
        checksum-valid IBAN) from pasted text, and answers in milliseconds
        where the old v2 strict search routinely hit 30s read timeouts.
        Response is shape-compatible with v2 (``items[]`` with id /
        businessName / regNumber / accountStatus / accountStatusUpdated /
        type) plus ``hasMore``. Falls back to v2 once per client instance if
        v3 is not deployed on the target host (404/405/501).
        """
        if not getattr(self, "_search_v2_only", False):
            try:
                return self._post(
                    "cstools_search",
                    "/api/cstools/v3/companies",
                    {"text": text, "page": 1, "pageSize": 50},
                )
            except BOError as exc:
                if exc.status_code not in (404, 405, 501):
                    raise
                self._search_v2_only = True  # v3 missing here — stop retrying it
        return self._post(
            "cstools_search",
            "/api/cstools/v2/companies",
            {"text": text, "page": 1, "pageSize": 50},
        )

    def cstools_short_info(self, company_id: str) -> dict:
        """GET ``/api/cstools/companies/{id}/short-info`` (stable status read)."""
        return self._get(
            "cstools_short_info",
            f"/api/cstools/companies/{company_id}/short-info",
        )

    def cstools_overview(self, company_id: str) -> dict:
        """GET ``/api/cstools/companies/{id}/overview`` -> type / address / legalStatus."""
        return self._get(
            "cstools_overview",
            f"/api/cstools/companies/{company_id}/overview",
        )

    def cdd_profile(self, company_id: str) -> dict:
        """GET ``/api/customerdossier/companies/{id}/cdd-profile`` -> PersonBirthdate."""
        return self._get(
            "cdd_profile",
            f"/api/customerdossier/companies/{company_id}/cdd-profile",
        )

    def wallets(self, company_uuid: str) -> dict:
        """GET ``/api/bank/wallets/`` -> ``{"items":[{iban,name,balance,currency,...}],...}``.

        The two query flags make each ``balance`` already net out debt + on-hold,
        so the available balance is simply ``Σ items[].balance`` (EUR wallets).
        """
        return self._get(
            "wallets",
            f"/api/bank/wallets/?page=1&companyId={company_uuid}"
            "&actualBalanceExcludingDebt=true&actualBalanceExcludingOnHold=true",
        )

    # -- transaction monitoring ---------------------------------------------

    def get_alerts(self, company_uuid: str) -> dict:
        """POST ``.../companies/{uuid}/alerts`` (body ``{"filters": {}}``).

        Open alert = ``resolvedOn`` is null. Returns ``{items, totalCount, ...}``.
        """
        return self._post(
            "get_alerts",
            f"/api/transactionmonitoring/companies/{company_uuid}/alerts",
            {"filters": {}},
        )

    def list_seizures(self, company_uuid: str) -> dict:
        """POST ``/api/transactionmonitoring/company/seizures`` -> ``{"seizures": [...]}``.

        The endpoint is paginated (default ``pageSize`` 10, max 124) and reports a
        ``totalCount``. Sending no paging params returns only the first 10
        seizures, so a company with more would silently lose the rest — and the
        declaration would omit real competing seizures. Fetch every page and
        return the full, concatenated ``seizures`` list under the same key.
        """
        page_size = 100  # under BO's 124 cap — one request covers most companies
        seizures: list = []
        page = 1
        while True:
            resp = self._post(
                "list_seizures",
                "/api/transactionmonitoring/company/seizures",
                {"companyId": company_uuid, "page": page, "pageSize": page_size},
            )
            batch = resp.get("seizures") or []
            seizures.extend(batch)
            total = resp.get("totalCount")
            # Stop when: the page was empty, the API reports no total (single
            # page), everything has been collected, or a hard page cap (loop
            # guard against a misbehaving endpoint) is reached.
            if not batch or total is None or len(seizures) >= total or page >= 200:
                break
            page += 1
        return {"seizures": seizures}

    def get_seizure(self, seizure_id: str) -> dict:
        """GET ``.../seizure/{id}`` -> detail incl. ``seizedAmount``, ``balance``, ``comment``."""
        return self._get(
            "get_seizure",
            f"/api/transactionmonitoring/seizure/{seizure_id}",
        )

    # -- user context (workspaces) --------------------------------------------
    # The BO "workspace" (FinomPayments / PnlFintech) is SERVER-SIDE user state
    # scoped to the INTTOKEN user — the same thing the portal's "Change
    # Workspace" does. These two calls are the only non-business-data POST in
    # this client: setting the active contexts is a session preference (bigi
    # widens it for the duration of one case and restores it afterwards).

    def whoami(self) -> dict:
        """GET ``/api/cstools/whoami`` -> profile incl. ``contexts`` (available
        workspaces) and ``activeContexts`` (currently active)."""
        return self._get("whoami", "/api/cstools/whoami")

    def set_user_contexts(self, contexts: list) -> dict:
        """POST ``/api/cstools/user-context/set`` — set the ACTIVE workspaces
        for the current user (session preference; no business data)."""
        return self._post(
            "set_user_contexts",
            "/api/cstools/user-context/set",
            {"userContexts": list(contexts or [])},
        )
