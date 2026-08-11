"""In-memory BO stub for tests — duck-types ``app.bo_client.BOClient``."""
from __future__ import annotations

from app.bo_client import BOError


class StubBO:
    """Configure per-company fixtures; unknown companies return empty shapes.

    fixtures = {
      company_uuid: {
        "search_items": [...],          # cstools_search hits (also by term via search_map)
        "short_info": {...},
        "overview": {...},
        "cdd": {...},
        "wallets": [...],
        "alerts": [...],
        "seizures": [...],              # listing rows
        "seizure_details": {id: {...}},
      }
    }
    search_map = {term: company_uuid} routes search terms to a company's items.
    search_items_map = {term: [items]} returns raw items for a term (multi-company).
    fail = set of method names that should raise BOError.
    """

    def __init__(self, fixtures: dict | None = None, search_map: dict | None = None,
                 fail: set[str] | None = None, search_items_map: dict | None = None,
                 profile: dict | None = None):
        self.fixtures = fixtures or {}
        self.search_map = search_map or {}
        self.search_items_map = search_items_map or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []
        # Single workspace by default -> the widening logic is a no-op.
        self.profile = profile or {"contexts": ["FinomPayments"],
                                   "activeContexts": ["FinomPayments"]}
        self.active_contexts = list(self.profile.get("activeContexts") or [])

    # -- helpers -------------------------------------------------------------

    def _fx(self, uuid: str) -> dict:
        return self.fixtures.get(uuid, {})

    def _maybe_fail(self, tool: str):
        if tool in self.fail:
            raise BOError(tool, 502, "stubbed failure")

    # -- API -------------------------------------------------------------------

    def cstools_search(self, text: str) -> dict:
        self.calls.append(("cstools_search", text))
        self._maybe_fail("cstools_search")
        term = (text or "").strip()
        if term in self.search_items_map:
            return {"items": self.search_items_map[term]}
        uuid = self.search_map.get(term)
        if uuid:
            return {"items": self._fx(uuid).get("search_items", [])}
        # direct-uuid lookup (used by _account_item fallback / _lookup_name)
        if text in self.fixtures:
            return {"items": self._fx(text).get("search_items", [])}
        return {"items": []}

    def cstools_short_info(self, company_id: str) -> dict:
        self.calls.append(("cstools_short_info", company_id))
        self._maybe_fail("cstools_short_info")
        if company_id not in self.fixtures:
            # Mirrors real BO: unknown UUIDs (e.g. a seizure entity's ID) 404.
            raise BOError("cstools_short_info", 404,
                          f"Company with ID = {company_id} not found")
        return self._fx(company_id).get("short_info", {})

    def cstools_overview(self, company_id: str) -> dict:
        self.calls.append(("cstools_overview", company_id))
        self._maybe_fail("cstools_overview")
        return self._fx(company_id).get("overview", {})

    def cdd_profile(self, company_id: str) -> dict:
        self.calls.append(("cdd_profile", company_id))
        self._maybe_fail("cdd_profile")
        return self._fx(company_id).get("cdd", {})

    def wallets(self, company_uuid: str) -> dict:
        self.calls.append(("wallets", company_uuid))
        self._maybe_fail("wallets")
        return {"items": self._fx(company_uuid).get("wallets", [])}

    def get_alerts(self, company_uuid: str) -> dict:
        self.calls.append(("get_alerts", company_uuid))
        self._maybe_fail("get_alerts")
        items = self._fx(company_uuid).get("alerts", [])
        return {"items": items, "totalCount": len(items)}

    def list_seizures(self, company_uuid: str) -> dict:
        self.calls.append(("list_seizures", company_uuid))
        self._maybe_fail("list_seizures")
        return {"seizures": self._fx(company_uuid).get("seizures", [])}

    def get_seizure(self, seizure_id) -> dict:
        self.calls.append(("get_seizure", str(seizure_id)))
        self._maybe_fail("get_seizure")
        for fx in self.fixtures.values():
            det = (fx.get("seizure_details") or {}).get(seizure_id)
            if det is not None:
                return det
        return {}

    def whoami(self) -> dict:
        self.calls.append(("whoami", ""))
        self._maybe_fail("whoami")
        return dict(self.profile)

    def set_user_contexts(self, contexts) -> dict:
        self.calls.append(("set_user_contexts", ",".join(contexts)))
        self._maybe_fail("set_user_contexts")
        self.active_contexts = list(contexts)
        return {}
