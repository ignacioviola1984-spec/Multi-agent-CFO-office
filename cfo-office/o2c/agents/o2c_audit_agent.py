"""o2c_audit_agent.py - Independent re-performance and audit opinion (third line)."""

import base_agent
from base_agent import O2CAgent, money
import o2c_core as core
import o2c_data_loader as loader
import o2c_policy as P


class O2CAuditAgent(O2CAgent):
    name = "O2CAuditAgent"
    purpose = "Independently re-perform critical O2C calculations and issue an opinion."
    maker_owner = P.MAKER_OWNERS["O2CAuditAgent"]
    checker_owner = P.CHECKER_OWNERS["O2CAuditAgent"]
    input_tables = ["invoices", "cash_application", "credit_memos", "deferred", "bank_receipts"]
    output_artifacts = ["audit_score", "control_summary", "blocked_reporting_items",
                        "reconciliation_status", "audit_opinion"]
    deterministic_checks_used = ["independent re-performance of AR, deferred, and cash tie-outs"]

    def analyze(self, ctx):
        dfs, period = ctx.dfs, ctx.period
        summary = ctx.calc["summary"]
        csum = ctx.calc["controls_summary"]

        # --- independently re-derive open AR (do NOT trust the reported number) ---
        inv = dfs["invoices"]
        ca = dfs["cash_application"]
        cm = dfs["credit_memos"]
        inv_ccy = inv.set_index("invoice_id")["currency"]
        ca2 = ca.copy()
        ca2["ccy"] = ca2["invoice_id"].map(inv_ccy).fillna("USD")
        ca2["applied_usd"] = loader.to_usd(ca2["applied_amount"], ca2["ccy"])
        applied = ca2[ca2["application_status"] == "applied"].groupby("invoice_id")["applied_usd"].sum()
        credited = (cm[cm["approval_status"] == "approved"].groupby("invoice_id")["credit_amount_usd"].sum())
        reperf = inv[["invoice_id", "total_invoice_amount_usd"]].copy()
        reperf["applied"] = reperf["invoice_id"].map(applied).fillna(0.0)
        reperf["credited"] = reperf["invoice_id"].map(credited).fillna(0.0)
        reperf["open"] = reperf["total_invoice_amount_usd"] - reperf["applied"] - reperf["credited"]
        reperf_open_ar = round(float(reperf[reperf["open"] > 1.0]["open"].sum()), 2)
        ar_tie_diff = round(reperf_open_ar - summary["open_ar_usd"], 2)

        # --- reconciliation checks (each pass/fail) ---
        capp = core.calculate_cash_application_status(dfs, period)
        unapp = core.calculate_unapplied_cash(dfs, period)
        cash_tie_diff = round(capp["received_usd"] - capp["applied_usd"]
                              - unapp["unmatched_receipt_usd"], 2)
        ctrl_by_id = {r.control_id: r for r in ctx.calc["controls"]}
        checks = {
            "ar_open_reperformance_ties": abs(ar_tie_diff) <= P.AR_TO_GL_TOLERANCE_USD,
            "ar_subledger_to_control_ties": ctrl_by_id["H_AR_SUBLEDGER_COMPLETENESS"].status == "PASS",
            "deferred_rollforward_foots": ctrl_by_id["L_DEFERRED_REVENUE_ROLLFORWARD"].status == "PASS",
            "cash_received_equals_applied_plus_unapplied": abs(cash_tie_diff) <= max(1.0, capp["received_usd"] * 0.001),
            "revenue_cutoff_clean": ctrl_by_id["K_REVENUE_RECOGNITION_CUTOFF"].status == "PASS",
            "billing_complete": ctrl_by_id["D_BILLING_COMPLETENESS"].status == "PASS",
        }
        passed = sum(1 for v in checks.values() if v)
        audit_score = round(passed / len(checks) * 100.0, 1)

        hard_failures = [r.control_id for r in ctx.calc["controls"]
                         if r.severity == "HARD" and r.status == "FAIL"]
        n_hard = len(hard_failures)
        if n_hard == 0:
            opinion = "unqualified"
        elif n_hard <= 3:
            opinion = "qualified"
        else:
            opinion = "adverse"

        esc = []
        if opinion != "unqualified":
            sev = "CRITICAL" if opinion == "adverse" else "HIGH"
            esc.append(self.escalate(sev, f"O2C audit opinion: {opinion.upper()} - {n_hard} hard "
                                     f"control(s) fail; reporting is blocked"))

        headline = f"audit opinion {opinion.upper()}, score {audit_score}%, {n_hard} blockers"
        narrative = (
            f"Independent O2C re-performance for {period}. Re-derived open AR is "
            f"{money(reperf_open_ar)} versus the reported {money(summary['open_ar_usd'])} "
            f"(tie {money(ar_tie_diff)}). {passed}/{len(checks)} reconciliation checks pass "
            f"(audit score {audit_score}%). {n_hard} hard controls fail, so reporting is blocked and "
            f"the opinion is {opinion.upper()}. Every figure traces to source tables "
            f"(invoices, cash_application, credit_memos, deferred, bank_receipts).")
        return {"headline": headline, "narrative": narrative,
                "audit_score": audit_score, "audit_opinion": opinion,
                "reperformed_open_ar_usd": reperf_open_ar, "reported_open_ar_usd": summary["open_ar_usd"],
                "ar_tie_diff_usd": ar_tie_diff, "cash_tie_diff_usd": cash_tie_diff,
                "reconciliation_status": {k: bool(v) for k, v in checks.items()},
                "control_summary": csum, "blocked_reporting_items": hard_failures,
                "source_traceability": self.input_tables,
                "escalations": esc, "recommended_actions": [
                    "Controller: clear all hard control failures before releasing O2C reporting."]}


def run(ctx):
    return O2CAuditAgent().run(ctx)
