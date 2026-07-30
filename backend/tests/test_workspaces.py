"""Cross-workspace widening (SOP: check FP and PNL) — app.workspaces."""
from __future__ import annotations

import pytest

from app.workspaces import all_workspaces
from tests.fixtures import IBAN, UUID, company, fields, raw_ticket
from tests.stub_bo import StubBO

BOTH = {"contexts": ["FinomPayments", "PnlFintech"], "activeContexts": ["FinomPayments"]}


def test_single_workspace_is_noop():
    stub = StubBO()
    with all_workspaces(stub) as ws:
        assert ws["switched"] is False and ws["error"] is None
    assert not [c for c in stub.calls if c[0] == "set_user_contexts"]


def test_widen_and_restore():
    stub = StubBO(profile=BOTH)
    with all_workspaces(stub) as ws:
        assert ws["switched"] is True
        assert stub.active_contexts == ["FinomPayments", "PnlFintech"]
    # Restored to the original selection afterwards.
    assert stub.active_contexts == ["FinomPayments"]
    sets = [c[1] for c in stub.calls if c[0] == "set_user_contexts"]
    assert sets == ["FinomPayments,PnlFintech", "FinomPayments"]


def test_already_all_active_is_noop():
    stub = StubBO(profile={"contexts": ["FinomPayments", "PnlFintech"],
                           "activeContexts": ["PnlFintech", "FinomPayments"]})
    with all_workspaces(stub) as ws:
        assert ws["switched"] is False
    assert not [c for c in stub.calls if c[0] == "set_user_contexts"]


def test_whoami_failure_degrades():
    stub = StubBO(fail={"whoami"})
    with all_workspaces(stub) as ws:
        assert ws["switched"] is False
        assert "whoami failed" in ws["error"]


def test_set_failure_degrades():
    stub = StubBO(profile=BOTH, fail={"set_user_contexts"})
    with all_workspaces(stub) as ws:
        assert ws["switched"] is False
        assert "could not widen" in ws["error"]


def test_restore_runs_even_on_exception():
    stub = StubBO(profile=BOTH)
    with pytest.raises(RuntimeError):
        with all_workspaces(stub):
            raise RuntimeError("boom")
    assert stub.active_contexts == ["FinomPayments"]   # restored in finally


def test_client_without_workspace_support_is_noop():
    class Bare:
        pass

    with all_workspaces(Bare()) as ws:
        assert ws["switched"] is False and ws["error"] is None


def test_pipeline_cross_workspace_hit(monkeypatch, db, client):
    """Company only findable once both workspaces are active -> S1 + warning."""
    from app import pipeline

    class CrossWorkspaceStub(StubBO):
        def cstools_search(self, text):
            # PNL company: invisible until PnlFintech is in the active set.
            if "PnlFintech" not in self.active_contexts:
                self.calls.append(("cstools_search", text))
                return {"items": []}
            return super().cstools_search(text)

    stub = CrossWorkspaceStub(fixtures={UUID: company()}, profile=dict(BOTH),
                              search_map={IBAN: UUID})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(company_uuid="", debtor_register_number="")
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["scenario"] == "S1"
    assert r["account"]["company_uuid"] == UUID
    assert any("across workspaces" in w for w in r["warnings"])
    assert stub.active_contexts == ["FinomPayments"]   # restored


def test_pipeline_single_workspace_no_warning(monkeypatch, db, client):
    from app import pipeline

    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket())
    assert not any("workspaces" in w for w in r["warnings"])
