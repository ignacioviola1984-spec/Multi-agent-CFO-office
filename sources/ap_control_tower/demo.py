"""
demo.py - reproducible, offline, no-API-key end-to-end evidence for the AP
Control Tower reference integration. A third party can run it from a fresh clone:

    python sources/ap_control_tower/demo.py

It reads the synthetic approved-proposal fixture, ingests it fail-closed into the
canonical layer, materializes a base vs a merged canonical, and runs the EXISTING
engine (`finance_core`) over both in an isolated subprocess (the same pattern as
`sources/reconcile/`). It then proves, from computed numbers (never hand-written):

  - the fixture is read and how many rows are imported;
  - totals by currency and the mapping by entity;
  - the resulting canonical records, all with status "open" (never "paid");
  - AP obligations rise by exactly the imported open total (Administration/AP);
  - the Treasury 13-week forecast outflow rises by the same total (Treasury);
  - Internal Controls stay green (n_fail unchanged) AND the >=25k proposal is
    correctly flagged for authorization review (control detects it);
  - because a proposal is NOT a journal entry, the close's subledger->GL control
    and the independent audit correctly flag the imported obligations as
    not-yet-posted (an open reconciling item = the imported total) - i.e. the
    adapter never touches the ledger and existing controls remain effective;
  - a module-owned manifest (sha256 + provenance) is written for audit;
  - no bank detail or tax id is persisted anywhere and no payment is executed;
  - an invalid row is rejected fail-closed (row + reason);
  - a replay / duplicate is rejected.

Exit code is 0 only when every check passes; non-zero on any violation.
"""

import copy
import datetime
import json
import os
import subprocess
import sys
import tempfile

_PKG = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_PKG)
_REPO = os.path.dirname(_SRC)
_ORCH = os.path.join(_REPO, "orchestration")
for _p in (_PKG, os.path.join(_SRC, "canonical")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contract
import adapter
import materialize
from connector import SyntheticConnector

PERIOD = "2026-05"
FIXTURE = os.path.join(_PKG, "fixtures", "approved_payment_proposal.csv")
ENTITY_MAP = os.path.join(_PKG, "fixtures", "entity_map.json")
OUT = None
TOL = 0.5                                       # USD rounding tolerance

_FAILS = []


def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _FAILS.append(msg)
    return cond


def _run_engine(data_dir):
    """Run finance_core over `data_dir` in an isolated subprocess and return the
    AP-relevant metrics as a dict (same isolation pattern as sources/reconcile)."""
    script = (
        "import sys, os, json\n"
        f"sys.path.insert(0, {_ORCH!r})\n"
        "import finance_core as fc\n"
        "p = os.environ['AP_PERIOD']\n"
        "apm = fc.ap_metrics(p); sub = fc.subledger_totals_usd(p)\n"
        "cc = fc.control_checks(p); fcast = fc.cash_forecast_13w()\n"
        "close = fc.close_reconciliations(p); au = fc.audit_procedures(p)\n"
        "ap_rec = next((r for r in close['recs'] if r['item']=='Accounts payable'), {})\n"
        "ap_find = next((f for f in au['findings'] if f['proc'].startswith('Accounts payable')), {})\n"
        "print(json.dumps({\n"
        "  'ap_open_total': apm['open_total'], 'ap_overdue': apm['overdue'],\n"
        "  'ap_upcoming_30d': apm['upcoming_30d'], 'ap_subledger': sub['ap'],\n"
        "  'cc_n_fail': cc['n_fail'], 'cc_n_exception': cc['n_exception'],\n"
        "  'cc_approval_exceptions': cc['approval_exceptions'],\n"
        "  'cc_approval_exceptions_total': cc['approval_exceptions_total'],\n"
        "  'forecast_outflow_total': sum(w['outflow'] for w in fcast['rows']),\n"
        "  'close_ap_diff': ap_rec.get('diff'), 'close_ap_status': ap_rec.get('status'),\n"
        "  'close_all_reconciled': close['all_reconciled'],\n"
        "  'audit_opinion': au['opinion'], 'audit_n_exceptions': au['n_exceptions'],\n"
        "  'audit_ap_ok': ap_find.get('ok')}))\n"
    )
    env = dict(os.environ)
    env.pop("FINANCE_DATA_DIR", None)
    env.pop("FINANCE_LATEST_PERIOD", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["FINANCE_DATA_DIR"] = os.path.abspath(data_dir)
    env["FINANCE_LATEST_PERIOD"] = PERIOD
    env["AP_PERIOD"] = PERIOD
    out = subprocess.run([sys.executable, "-c", script], env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"finance_core subprocess failed:\n{out.stderr}")
    return json.loads(out.stdout.strip())


def main():
    global OUT
    try:  # portable UTF-8 output (Windows consoles default to cp1252)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 78)
    print("AP Control Tower -> canonical ap_invoices : reference integration demo")
    print("=" * 78)
    # A unique system-temp run keeps evidence reproducible on Windows/OneDrive,
    # whose synced directories may be read-only reparse points that rmtree cannot
    # safely remove. AP_CT_DEMO_ROOT may choose another parent, but never an
    # existing run directory.
    demo_root = os.environ.get("AP_CT_DEMO_ROOT") or tempfile.gettempdir()
    os.makedirs(demo_root, exist_ok=True)
    OUT = tempfile.mkdtemp(prefix="cfo-office-apct-demo-", dir=demo_root)
    print(f"evidence run directory: {OUT}")

    # --- 1) read fixture + explicit entity mapping ---------------------------
    print("\n[1] Read the synthetic approved-proposal export (AP Control Tower shape)")
    columns, rows = adapter.read_export(FIXTURE)
    emap = adapter.EntityMap.from_json_file(ENTITY_MAP)
    print(f"  fixture: {os.path.relpath(FIXTURE, _REPO)}")
    print(f"  columns: {len(columns)} (contract requires {len(contract.PAYMENT_EXPORT_COLUMNS)})")
    print(f"  rows in file: {len(rows)}")
    check(columns == contract.PAYMENT_EXPORT_COLUMNS, "export matches the 17-column contract exactly")

    # --- 2) ingest fail-closed into a base canonical -------------------------
    print("\n[2] Ingest (fail-closed) and map to canonical ap_invoices")
    base = SyntheticConnector().canonical_tables()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = adapter.ingest(base, PERIOD, export_path=FIXTURE, entity_map=emap,
                            now_iso=now_iso)
    imported = result["canonical_rows"]
    print(f"  imported: {result['imported_count']} approved proposals")
    print(f"  totals by currency: {result['totals_by_currency']}")
    print(f"  by entity: {json.dumps(result['by_entity'])}")
    print("  canonical ap_invoices records produced:")
    for r in imported:
        print(f"    {r['bill_id']:>16}  {r['entity_id']:>3}  {r['vendor']:<28} "
              f"{r['currency']} {r['amount_local']:>10}  due {r['due_date']}  status={r['status']}")

    check(result["imported_count"] == 4, "imported the 3+ approved proposals (4)")
    check({r["currency"] for r in imported} == {"EUR", "USD"}, "covers at least two currencies")
    check({r["entity_id"] for r in imported} == {"DE", "US"}, "covers at least two explicitly mapped entities")
    check(all(r["status"] == "open" for r in imported), "every record is OPEN, never 'paid'")
    check(all(c not in r for r in imported for c in contract.BANK_COLUMNS),
          "no bank columns on the canonical records")

    # expected USD delta, computed from the fixture + base FX (not hand-written)
    fx = {r["currency"].upper(): float(r["units_per_usd"])
          for r in base["fx_rates"] if r["period"] == PERIOD}
    fx["USD"] = 1.0
    imported_usd = sum(float(r["amount_local"]) / fx[r["currency"]] for r in imported)
    print(f"  imported open total (USD @ {PERIOD} FX): {imported_usd:,.2f}")

    # --- 3) materialize base vs merged canonical, run the REAL engine on both -
    print("\n[3] Run the existing finance_core over base vs merged canonical (subprocess)")
    base_dir = os.path.join(OUT, "canonical_base")
    merged_dir = os.path.join(OUT, "canonical_merged")
    materialize.write_canonical_tables(base, base_dir)
    materialize.write_canonical_tables(result["merged_canonical"], merged_dir)
    before = _run_engine(base_dir)
    after = _run_engine(merged_dir)

    d_open = after["ap_open_total"] - before["ap_open_total"]
    d_sub = after["ap_subledger"] - before["ap_subledger"]
    d_outflow = after["forecast_outflow_total"] - before["forecast_outflow_total"]
    d_appr = after["cc_approval_exceptions"] - before["cc_approval_exceptions"]
    d_appr_total = after["cc_approval_exceptions_total"] - before["cc_approval_exceptions_total"]
    d_close_ap = (after["close_ap_diff"] or 0.0) - (before["close_ap_diff"] or 0.0)
    print(f"  AP obligations (ap_metrics.open_total): {before['ap_open_total']:,.2f} -> "
          f"{after['ap_open_total']:,.2f}  (delta {d_open:,.2f})")
    print(f"  Treasury 13-week outflow total:         {before['forecast_outflow_total']:,.2f} -> "
          f"{after['forecast_outflow_total']:,.2f}  (delta {d_outflow:,.2f})")
    print(f"  Internal Controls n_fail:               {before['cc_n_fail']} -> {after['cc_n_fail']}")
    print(f"  C5 authorization-review items:          {before['cc_approval_exceptions']} -> "
          f"{after['cc_approval_exceptions']}  (delta {d_appr}, delta total {d_appr_total:,.2f})")
    print(f"  Close AP subledger vs GL diff:          {before['close_ap_diff']} -> "
          f"{after['close_ap_diff']}  (status {after['close_ap_status']})")
    print(f"  Independent audit opinion:              {before['audit_opinion']} -> {after['audit_opinion']}")

    check(abs(d_open - imported_usd) <= TOL, "AP obligations rose by exactly the imported open total (Administration/AP)")
    check(abs(d_sub - imported_usd) <= TOL, "AP subledger rose by exactly the imported open total")
    check(abs(d_outflow - imported_usd) <= TOL, "Treasury 13-week outflow rose by the imported total (Treasury consumes AP)")
    check(after["cc_n_fail"] == before["cc_n_fail"] == 0, "Internal Controls hard gate stays green (n_fail unchanged, 0)")
    check(d_appr == 1 and abs(d_appr_total - 28000.0) <= TOL,
          "the >=25k approved proposal is flagged for authorization review (C5 detects it)")
    check(abs(d_close_ap - imported_usd) <= TOL and after["close_ap_status"] == "OPEN ITEM",
          "close subledger->GL control flags the proposal as not-yet-posted (= imported total)")
    check(after["audit_ap_ok"] is False and after["audit_opinion"] in ("qualified", "adverse"),
          "independent audit detects the unbooked proposal (P2 AP tie-out)")

    # --- 4) write + verify the module-owned audit evidence -------------------
    print("\n[4] Write the module-owned audit evidence (trace + manifest)")
    paths = adapter.write_outputs(os.path.join(OUT, "evidence"), result)
    manifest = result["manifest"]
    print(f"  trace:    {os.path.relpath(paths['trace'], _REPO)}")
    print(f"  manifest: {os.path.relpath(paths['manifest'], _REPO)}")
    print(f"  source sha256: {manifest['source_sha256']}")
    print(f"  provenance rows: {len(manifest['provenance'])} (export row -> bill_id -> entity)")
    blob = json.dumps(imported) + json.dumps(result["trace_rows"]) + json.dumps(manifest)
    check(len(manifest["provenance"]) == result["imported_count"], "provenance covers every imported row")
    check(manifest["payment_executed"] is False and manifest["posted_to_ledger"] is False,
          "manifest asserts: no payment executed, nothing posted to the ledger")
    check(not any(s in blob for s in ("SYNTHETIC", "SYNTHXX", "SYNTH-TAX", "not a real institution")),
          "no bank detail or tax id leaked into canonical / trace / manifest")

    # --- 5) fail-closed: an invalid row is rejected with row + reason --------
    print("\n[5] Fail-closed: an invalid export is rejected (row + reason)")
    bad = copy.deepcopy(rows)
    bad[0]["estado"] = "rechazada"          # not approved
    bad[1]["importe"] = "-5"                # non-positive
    bad[2]["moneda"] = "JPY"                # not FX-covered
    bad[3]["vencimiento"] = "not-a-date"    # invalid date
    rejected = False
    try:
        adapter.ingest(base, PERIOD, columns=columns, rows=bad, entity_map=emap)
    except adapter.APControlTowerError as exc:
        rejected = True
        for p in exc.problems:
            print(f"    rejected row {p.row}: {p.reason}")
        leaked = any(s in str(exc) for s in ("SYNTHETIC", "SYNTHXX", "SYNTH-TAX", "not a real institution"))
        check(not leaked, "rejection message contains no bank detail or tax id")
    check(rejected, "invalid export raised APControlTowerError (fail-closed)")

    # --- 6) replay / duplicate protection -----------------------------------
    print("\n[6] Replay / duplicate protection")
    dup_rejected = False
    try:  # re-ingest the same file against the ALREADY-merged canonical
        adapter.ingest(result["merged_canonical"], PERIOD, export_path=FIXTURE, entity_map=emap)
    except adapter.APControlTowerError:
        dup_rejected = True
    check(dup_rejected, "re-ingesting the same proposals is rejected as duplicates (idempotent / no double-count)")

    # --- verdict -------------------------------------------------------------
    print("\n" + "=" * 78)
    if _FAILS:
        print(f"RESULT: FAIL ({len(_FAILS)} check(s) failed)")
        for m in _FAILS:
            print(f"  - {m}")
        return 1
    print("RESULT: PASS - AP Control Tower proposals are visible as canonical AP "
          "obligations,\n        auditable, and fail-closed; no payment executed, "
          "nothing posted to the ledger.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
