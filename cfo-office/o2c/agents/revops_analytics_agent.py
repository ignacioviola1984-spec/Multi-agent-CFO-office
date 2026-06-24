"""revops_analytics_agent.py - Executive Revenue Operations insight."""

import base_agent
from base_agent import O2CAgent, money
import o2c_policy as P


class RevOpsAnalyticsAgent(O2CAgent):
    name = "RevOpsAnalyticsAgent"
    purpose = "Build the executive bookings-to-cash bridge and explain the bottlenecks."
    maker_owner = P.MAKER_OWNERS["RevOpsAnalyticsAgent"]
    checker_owner = P.CHECKER_OWNERS["RevOpsAnalyticsAgent"]
    input_tables = ["contracts", "invoices", "revenue", "cash_application"]
    output_artifacts = ["bookings_to_cash_bridge", "dso_explanation", "cash_conversion_bottlenecks",
                        "revenue_leakage_by_source", "throughput_metrics", "board_narrative"]
    deterministic_checks_used = ["(reads all O2C metrics)"]

    def analyze(self, ctx):
        s = ctx.calc["summary"]
        bridge = ctx.calc["bridge"]
        dfs = ctx.dfs

        # cash conversion bottlenecks: where cash is stuck between billing and bank
        bottlenecks = [
            {"stage": "Unbilled (leakage)", "amount_usd": s["unbilled_revenue_usd"]},
            {"stage": "Overdue AR", "amount_usd": s["overdue_ar_usd"]},
            {"stage": "Disputed (blocked)", "amount_usd": s["disputed_ar_usd"]},
            {"stage": "Unapplied cash", "amount_usd": s["unapplied_cash_usd"]},
        ]
        bottlenecks.sort(key=lambda x: -x["amount_usd"])

        throughput = {
            "opportunities": int(len(dfs["opportunities"])),
            "contracts": int(len(dfs["contracts"])),
            "orders": int(len(dfs["orders"])),
            "invoices": int(len(dfs["invoices"])),
            "payments": int(len(dfs["payments"])),
        }
        dso_expl = (f"DSO is {s['dso']} days vs a best-possible {s['best_possible_dso']} days. "
                    f"The {round(s['dso'] - s['best_possible_dso'], 1)}-day gap is overdue collections: "
                    f"{money(s['overdue_ar_usd'])} of AR is past due, with {money(s['ar_90_plus_usd'])} "
                    f"in the 90+ buckets.")

        esc, actions = [], []
        top = bottlenecks[0]
        actions.append(f"Attack the largest bottleneck first: {top['stage']} "
                       f"({money(top['amount_usd'])}).")
        actions.append("Close billing leakage and unapplied cash to convert AR to bank faster.")

        headline = (f"Bookings {money(bridge['bookings_usd'])} -> billings {money(bridge['billings_usd'])} "
                    f"-> revenue {money(bridge['recognized_revenue_usd'])} -> cash {money(bridge['cash_collected_usd'])}")
        board_narrative = (
            f"Revenue operations, {ctx.period}. The bookings-to-cash bridge: {money(bridge['bookings_usd'])} "
            f"booked, {money(bridge['billings_usd'])} billed, {money(bridge['recognized_revenue_usd'])} "
            f"recognized, {money(bridge['cash_collected_usd'])} collected. {dso_expl} "
            f"The biggest cash-conversion bottleneck is {top['stage'].lower()} at {money(top['amount_usd'])}. "
            f"Billing completeness is {s['billing_completeness_pct']}% and invoice accuracy "
            f"{s['invoice_accuracy_pct']}%, so revenue leakage and rework are the primary throughput drags.")
        return {"headline": headline, "narrative": board_narrative,
                "bookings_to_cash_bridge": bridge["bridge"],
                "dso_explanation": dso_expl,
                "cash_conversion_bottlenecks": bottlenecks,
                "revenue_leakage_by_source": {"unbilled_usd": s["unbilled_revenue_usd"],
                    "mismatch_rework": round(100 - s["invoice_accuracy_pct"], 2)},
                "throughput_metrics": throughput,
                "board_narrative": board_narrative,
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return RevOpsAnalyticsAgent().run(ctx)
