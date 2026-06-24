"""
cfo_o2c_bridge.py - Run the Order-to-Cash control tower as a sub-orchestration
under the CFO orchestrator, and fold its result into the shared CFO state.

This is what makes the O2C tower a sub-orchestrator of the CFO agent at runtime:
cfo_orchestrator.py calls run_o2c_suborchestration() during the close, the O2C
control tower runs deterministically (no LLM, no API key), and its status,
metrics, and escalations land in the same CFOContext the close agents write to,
so the consolidated board pack and the CFO gate see Order-to-Cash alongside the
month-end close. Kept in its own module (no Anthropic import) so it is testable
without an API key.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))      # cfo-office
O2C = os.path.join(HERE, "o2c")
for _p in (O2C, os.path.join(O2C, "agents"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_policy as P            # noqa: E402
import o2c_orchestrator as o2c_orch  # noqa: E402


def _money(x):
    return f"USD {x:,.0f}"


def _section(period, meta, csum, s):
    """A deterministic Order-to-Cash block for the consolidated board pack."""
    return (
        f"## Order-to-Cash (sub-orchestration, {period})\n"
        f"- Status: {meta['final_status']} | audit opinion: {meta['audit_opinion'].upper()} "
        f"| control pass rate: {csum['control_pass_rate_pct']}%\n"
        f"- Hard control failures: {csum['hard_failures']} (block O2C reporting) | "
        f"soft warnings: {csum['soft_warnings']}\n"
        f"- Open AR {_money(s['open_ar_usd'])} | Overdue {_money(s['overdue_ar_usd'])} | "
        f"DSO {s['dso']}d | Unbilled {_money(s['unbilled_revenue_usd'])} | "
        f"Unapplied {_money(s['unapplied_cash_usd'])} | Disputed {_money(s['disputed_ar_usd'])}\n"
        f"- Expected cash 13 weeks: {_money(s['expected_cash_13w_usd'])}\n"
    )


def run_o2c_suborchestration(ctx, period=P.DEFAULT_PERIOD, output_dir=None):
    """Run the O2C control tower and record its result in the CFO shared state.

    Stores an "Order-to-Cash" entry (status, metrics, escalations, board section)
    and returns it. Deterministic; the numbers come from o2c_core, not an LLM.
    """
    ctx.audit("Order-to-Cash", "start", f"O2C control tower sub-orchestration {period}")
    o2c_ctx, meta = o2c_orch.run(period=period, output_dir=output_dir or o2c_orch.DEFAULT_OUTPUT_DIR,
                                 fail_on_hard=False, verbose=False)
    s = o2c_ctx.calc["summary"]
    csum = o2c_ctx.calc["controls_summary"]

    esc = []
    if csum["hard_failures"] > 0:
        esc.append(["CRITICAL", f"O2C reporting BLOCKED: {csum['hard_failures']} hard control "
                    f"failures (DSO {s['dso']}d, overdue {_money(s['overdue_ar_usd'])}, "
                    f"unbilled {_money(s['unbilled_revenue_usd'])})"])
    elif csum["soft_warnings"] > 0:
        esc.append(["HIGH", f"O2C passed with {csum['soft_warnings']} soft warnings "
                    f"(DSO {s['dso']}d, overdue {_money(s['overdue_ar_usd'])})"])

    payload = {
        "status": meta["final_status"],
        "hard_failures": csum["hard_failures"],
        "soft_warnings": csum["soft_warnings"],
        "control_pass_rate": csum["control_pass_rate_pct"],
        "audit_opinion": meta["audit_opinion"],
        "metrics": {k: s[k] for k in ("open_ar_usd", "overdue_ar_usd", "dso",
                                      "unbilled_revenue_usd", "unapplied_cash_usd",
                                      "disputed_ar_usd", "expected_cash_13w_usd",
                                      "billings_usd", "cash_collected_usd")},
        "escalations": esc,
        "section": _section(period, meta, csum, s),
    }
    ctx.put("Order-to-Cash", payload)
    ctx.audit("Order-to-Cash", "ok",
              f"status {meta['final_status']}, {csum['hard_failures']} hard fail, "
              f"audit {meta['audit_opinion']}")
    return payload


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    from shared_state import CFOContext
    for per in ("2026-05", "2026-06"):
        ctx = CFOContext()
        r = run_o2c_suborchestration(ctx, per)
        print(f"{per}: {r['status']} | {r['hard_failures']} hard fail | audit {r['audit_opinion']}")
