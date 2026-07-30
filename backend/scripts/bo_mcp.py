"""finom-bo — local MCP server exposing READ-ONLY Finom Back-Office tools.

Runs on the operator's laptop (which has VPN access to BO) and lets Claude
query BO directly during development. Credentials come from ``backend/.env``
(BO_BASE_URL / BO_INTTOKEN) and, for the optional Jira tool, from the bigi
settings DB / env fallbacks. The token is used server-side only and is never
returned by any tool.

Setup (once):

    cd bigi/backend && source .venv/bin/activate && pip install mcp

Then register in ~/Library/Application Support/Claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "finom-bo": {
          "command": "/Users/s.mammadov/Documents/Gitlab/feizure/bigi/backend/.venv/bin/python3",
          "args": ["/Users/s.mammadov/Documents/Gitlab/feizure/bigi/backend/scripts/bo_mcp.py"]
        }
      }
    }

…and fully restart the Claude desktop app (Cmd+Q, reopen).

Every tool is a thin wrapper over ``app.bo_client.BOClient`` — the exact same
read-only client bigi itself uses. There is deliberately no write tool.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
# app.config resolves .env / bigi.db relative to the CWD; pin it to backend/.
os.chdir(_BACKEND)

# The high-level server class moved across SDK generations:
#   mcp >= 2.0  -> mcp.server.MCPServer
#   mcp 1.x     -> mcp.server.fastmcp.FastMCP
#   fastmcp pkg -> fastmcp.FastMCP
try:
    from mcp.server import MCPServer as FastMCP  # noqa: E402  (mcp >= 2.0)
except ImportError:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: E402  (mcp 1.x)
    except ImportError:
        try:
            from fastmcp import FastMCP  # noqa: E402  (standalone package)
        except ImportError:
            print(
                "bo_mcp: the MCP SDK is missing in THIS venv. Install it with:\n"
                f"  {_BACKEND}/.venv/bin/pip install -U mcp\n"
                "then fully restart the Claude app.",
                file=sys.stderr,
            )
            raise SystemExit(1)

from app.bo_client import BOClient, BOError  # noqa: E402
from app.config import get_settings  # noqa: E402

mcp = FastMCP("finom-bo")


def _client() -> BOClient:
    s = get_settings()
    return BOClient(s.bo_base_url, s.bo_inttoken, timeout=30.0)


def _call(fn, *args):
    try:
        return fn(*args)
    except BOError as exc:
        return {"error": True, "tool": exc.tool, "status_code": exc.status_code,
                "body": str(exc.body)[:2000]}


@mcp.tool()
def bo_cstools_search(text: str) -> dict:
    """Search companies by IBAN / name / register number (POST /api/cstools/v2/companies)."""
    return _call(_client().cstools_search, text)


@mcp.tool()
def bo_short_info(company_id: str) -> dict:
    """Company short-info (GET /api/cstools/companies/{id}/short-info) — status object, banners, details."""
    return _call(_client().cstools_short_info, company_id)


@mcp.tool()
def bo_overview(company_id: str) -> dict:
    """Company overview (GET /api/cstools/companies/{id}/overview) — type, address, legal form."""
    return _call(_client().cstools_overview, company_id)


@mcp.tool()
def bo_cdd_profile(company_id: str) -> dict:
    """CDD profile (GET /api/customerdossier/companies/{id}/cdd-profile) — nested sections incl. PersonBirthdate."""
    return _call(_client().cdd_profile, company_id)


@mcp.tool()
def bo_wallets(company_id: str) -> dict:
    """Wallets with balances net of debt/on-hold (GET /api/bank/wallets/?companyId=…)."""
    return _call(_client().wallets, company_id)


@mcp.tool()
def bo_alerts(company_id: str) -> dict:
    """Transaction-monitoring alerts (POST …/companies/{id}/alerts) — rules, resolvedOn, status."""
    return _call(_client().get_alerts, company_id)


@mcp.tool()
def bo_list_seizures(company_id: str) -> dict:
    """All seizures for a company, every page (POST …/company/seizures)."""
    return _call(_client().list_seizures, company_id)


@mcp.tool()
def bo_get_seizure(seizure_id: str) -> dict:
    """One seizure's detail (GET …/seizure/{id}) — caseNumber, comment, amounts, balance."""
    return _call(_client().get_seizure, seizure_id)


@mcp.tool()
def jira_issue(issue_key: str) -> dict:
    """Fetch a Jira issue's summary + description (read-only; uses the Jira
    credentials configured in bigi's Settings / env)."""
    from app.db import SessionLocal, init_db
    from app.jira import fetch_issue
    from app.settings_store import jira_config

    init_db()
    db = SessionLocal()
    try:
        return fetch_issue(jira_config(db), issue_key)
    except Exception as exc:  # noqa: BLE001 — surface, never crash the server
        return {"error": True, "detail": f"{type(exc).__name__}: {str(exc)[:500]}"}
    finally:
        db.close()


@mcp.tool()
def case_trace(issue_key: str, company_uuid: str = "", use_llm: bool = False) -> dict:
    """Fetch a Jira ticket and run bigi's FULL read-only pipeline on it
    (parse -> identify -> confirm -> alerts -> seizures -> balance -> scenario
    -> document). Returns the decision trace incl. the composed German
    document. ``company_uuid`` re-runs with an operator-picked account;
    ``use_llm=False`` forces the deterministic composer (fast, stable)."""
    from app.db import SessionLocal, init_db
    from app.jira import fetch_issue
    from app.pipeline import run_pipeline
    from app.settings_store import jira_config
    from app.trace import build_trace

    init_db()
    db = SessionLocal()
    try:
        issue = fetch_issue(jira_config(db), issue_key)
        if not use_llm:
            # Force deterministic compose by masking the LLM key for this run.
            from app import pipeline as _p
            real = _p.llm_config
            _p.llm_config = lambda _db: {"provider": "openai", "model": "", "api_key": ""}
            try:
                result = run_pipeline(db, issue["description"], company_uuid or None)
            finally:
                _p.llm_config = real
        else:
            result = run_pipeline(db, issue["description"], company_uuid or None)
        trace = build_trace(result, include_document=True)
        trace["jira"] = {"key": issue["key"], "summary": issue["summary"]}
        return trace
    except Exception as exc:  # noqa: BLE001 — surface, never crash the server
        return {"error": True, "detail": f"{type(exc).__name__}: {str(exc)[:800]}"}
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
