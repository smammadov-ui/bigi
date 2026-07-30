"""Read-only BO endpoint probe — run LOCALLY (on VPN) from bigi/backend:

    python3 scripts/bo_probe.py                    # search "test", probe first hit
    python3 scripts/bo_probe.py "ACME GmbH"        # search a real term
    python3 scripts/bo_probe.py --uuid <company>   # probe a known company UUID

Reads BO_BASE_URL/BO_INTTOKEN from backend/.env (or the environment). Output is
SANITIZED by design — statuses, key names, counts, and assumption checks only.
No token, no IBANs, no names, no amounts are printed, so the output is safe to
share when reporting problems.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bo_client import BOClient, BOError, is_processing, status_name  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.matching import _cdd_dob, _item_from_short_info, status_bucket  # noqa: E402


def fail_str(e: BOError) -> str:
    return f"FAIL status={e.status_code} body[:150]={str(e.body)[:150]!r}"


def check(label: str, ok: bool) -> None:
    print(f"    [{'ok' if ok else '??'}] {label}")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    uuid = ""
    if "--uuid" in args:
        uuid = args[args.index("--uuid") + 1]
        term = ""
    else:
        term = args[0] if args else "test"

    s = get_settings()
    if not s.bo_base_url or not s.bo_inttoken:
        print("BO_BASE_URL / BO_INTTOKEN not set (backend/.env or environment).")
        return 2
    c = BOClient(s.bo_base_url, s.bo_inttoken, timeout=20.0)
    print(f"probing {s.bo_base_url} (token: set, {len(s.bo_inttoken)} chars)\n")

    # -- 1. cstools_search ----------------------------------------------------
    if not uuid:
        try:
            out = c.cstools_search(term)
            items = out.get("items") or []
            print(f"1. cstools_search({term!r}): OK — top keys {sorted(out.keys())[:8]}, items={len(items)}")
            if items:
                it = items[0]
                print(f"    item keys: {sorted(it.keys())}")
                check("item has id", bool(it.get("id")))
                check("item has businessName", "businessName" in it)
                check("item has accountStatus", "accountStatus" in it)
                check("item has accountStatusUpdated", "accountStatusUpdated" in it)
                check("item has type (Company/Freelancer)", "type" in it)
                check("item has regNumber", "regNumber" in it)
                uuid = str(it.get("id") or "")
                st = str(it.get("accountStatus") or "")
                print(f"    accountStatus value maps to bucket: {status_bucket(st)} (raw present: {bool(st)})")
            else:
                print("    no items — pass a real search term (company name / IBAN / reg number)")
                return 0
        except BOError as e:
            print(f"1. cstools_search: {fail_str(e)}")
            return 1

    print(f"\nprobing company {uuid[:8]}…\n")

    # -- 2. short-info ----------------------------------------------------------
    try:
        si = c.cstools_short_info(uuid)
        print(f"2. cstools_short_info: OK — top keys {sorted(si.keys())[:12]}")
        item = _item_from_short_info(si, uuid)
        check("accountStatus resolvable (status.accountStatus / paymentAccountStatus.status)",
              bool(item.get("accountStatus")))
        check("type present", bool(item.get("type")))
        check("accountStatusUpdated present", bool(item.get("accountStatusUpdated")))
    except BOError as e:
        print(f"2. cstools_short_info: {fail_str(e)} (matching falls back to search — OK if gated)")

    # -- 3. overview --------------------------------------------------------------
    try:
        ov = c.cstools_overview(uuid)
        print(f"3. cstools_overview: OK — top keys {sorted(ov.keys())[:12]}")
        check("type present", bool(ov.get("type")))
        addr = ov.get("address")
        check("address present", bool(addr))
        if isinstance(addr, dict):
            print(f"    address keys: {sorted(addr.keys())}")
            import re
            flat = " ".join(str(v) for v in addr.values() if isinstance(v, (str, int)))
            check("address contains a 5-digit postcode (confirmation rule)",
                  bool(re.search(r"\b\d{5}\b", flat)))
    except BOError as e:
        print(f"3. cstools_overview: {fail_str(e)}")

    # -- 4. cdd-profile -------------------------------------------------------------
    try:
        cdd = c.cdd_profile(uuid)
        print(f"4. cdd_profile: OK — top keys {sorted(cdd.keys())[:12]}")
        check("PersonBirthdate extractable (nested walk)", bool(_cdd_dob(cdd)))
    except BOError as e:
        print(f"4. cdd_profile: {fail_str(e)}")

    # -- 5. wallets --------------------------------------------------------------------
    try:
        w = c.wallets(uuid)
        items = w.get("items") or []
        print(f"5. wallets: OK — top keys {sorted(w.keys())[:8]}, items={len(items)}")
        if items:
            print(f"    wallet keys: {sorted(items[0].keys())}")
            for k in ("iban", "name", "balance", "currency"):
                check(f"wallet has {k}", k in items[0])
            currencies = sorted({str(x.get('currency') or '?') for x in items})
            print(f"    currencies: {currencies}")
            check("a wallet named 'Main' exists (seized-IBAN derivation)",
                  any(str(x.get('name', '')).strip().lower() == 'main' for x in items))
    except BOError as e:
        print(f"5. wallets: {fail_str(e)}")

    # -- 6. alerts ---------------------------------------------------------------------
    try:
        al = c.get_alerts(uuid)
        items = al.get("items") or []
        print(f"6. get_alerts: OK — top keys {sorted(al.keys())[:8]}, items={len(items)}")
        if items:
            print(f"    alert keys: {sorted(items[0].keys())}")
            check("alert has rules", "rules" in items[0])
            check("alert has resolvedOn (open = null)", "resolvedOn" in items[0])
    except BOError as e:
        print(f"6. get_alerts: {fail_str(e)}")

    # -- 7. seizures -------------------------------------------------------------------
    try:
        sz = c.list_seizures(uuid)
        rows = sz.get("seizures") or []
        proc = [r for r in rows if is_processing(r)]
        print(f"7. list_seizures: OK — rows={len(rows)}, Processing={len(proc)}")
        if rows:
            print(f"    row keys: {sorted(rows[0].keys())}")
            statuses = sorted({status_name(r.get('status')) or '?' for r in rows})
            print(f"    statuses seen: {statuses}")
            sid = rows[0].get("id")
            if sid is not None:
                det = c.get_seizure(sid)
                print(f"8. get_seizure: OK — keys {sorted(det.keys())[:16]}")
                for k in ("caseNumber", "created", "comment", "seizedAmount", "amount"):
                    check(f"detail has {k}", k in det)
                check("detail has balance.clientTotal",
                      isinstance(det.get("balance"), dict) and "clientTotal" in det["balance"])
                for k in ("issuedBy", "issuedOn", "businessName"):
                    check(f"detail has {k} (structured T2 bullet)", k in det)
        else:
            print("    (no seizures on this company — get_seizure skipped)")
    except BOError as e:
        print(f"7. list_seizures: {fail_str(e)}")

    print("\ndone — every call above was read-only. Paste this output back if anything shows FAIL/??")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
