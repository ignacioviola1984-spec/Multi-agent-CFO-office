"""
run_o2c_control_tower.py - One-command entrypoint for the O2C / RevOps control tower.

Runs the Order-to-Cash control tower for the default period and prints the
headline: overall status, key metrics, hard control failures, the top 10 issues,
and where the outputs were written. No API keys, no external services.

    python run_o2c_control_tower.py
    python run_o2c_control_tower.py --period 2026-05
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.join(HERE, "cfo-office", "o2c")
for _p in (O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_policy as P            # noqa: E402
import o2c_orchestrator as orch   # noqa: E402


def _m(x):
    return f"USD {x:,.0f}"


def main():
    ap = argparse.ArgumentParser(description="Run the O2C / RevOps control tower")
    ap.add_argument("--period", default=P.DEFAULT_PERIOD)
    args = ap.parse_args()

    ctx, meta = orch.run(period=args.period, verbose=False)
    s = ctx.calc["summary"]
    csum = ctx.calc["controls_summary"]

    print("=" * 64)
    print(f"  O2C / REVENUE OPERATIONS CONTROL TOWER  |  period {args.period}")
    print("=" * 64)
    print(f"  STATUS: {meta['final_status']}   (audit opinion: {meta['audit_opinion'].upper()}, "
          f"score {meta['audit_score']}%)")
    print(f"  Control pass rate: {csum['control_pass_rate_pct']}%   "
          f"hard failures: {csum['hard_failures']}   soft warnings: {csum['soft_warnings']}")
    print("\n  METRICS")
    print(f"    Open AR              {_m(s['open_ar_usd'])}")
    print(f"    Overdue AR           {_m(s['overdue_ar_usd'])}")
    print(f"    DSO                  {s['dso']} days (best possible {s['best_possible_dso']})")
    print(f"    Expected cash 13w    {_m(s['expected_cash_13w_usd'])}")
    print(f"    Unbilled / leakage   {_m(s['unbilled_revenue_usd'])}")
    print(f"    Unapplied cash       {_m(s['unapplied_cash_usd'])}")
    print(f"    Disputed AR          {_m(s['disputed_ar_usd'])} ({s['disputed_ar_pct']}%)")
    print(f"    Credit breach        {_m(s['credit_breach_amount_usd'])}")
    print(f"    Bookings->Billings->Revenue->Cash  "
          f"{_m(s['bookings_usd'])} -> {_m(s['billings_usd'])} -> "
          f"{_m(s['recognized_revenue_usd'])} -> {_m(s['cash_collected_usd'])}")

    hard_fail = [r for r in ctx.calc["controls"] if r.severity == "HARD" and r.status == "FAIL"]
    print(f"\n  HARD CONTROL FAILURES ({len(hard_fail)}) - these BLOCK reporting")
    for r in hard_fail:
        print(f"    [FAIL] {r.control_id:32} {r.failing_record_count:>4} items  {_m(r.failing_amount_usd)}")

    print("\n  TOP 10 ISSUES")
    for i, e in enumerate(ctx.escalations()[:10], 1):
        print(f"    {i:>2}. [{e['severity']}] ({e['agent']}) {e['message']}")

    out = orch.DEFAULT_OUTPUT_DIR
    print(f"\n  OUTPUTS written to {out}:")
    for fn in meta["output_files"]:
        print(f"    - {fn}")
    print("\n  (deterministic numbers; agents diagnose and narrate but never invent a figure)")


if __name__ == "__main__":
    main()
