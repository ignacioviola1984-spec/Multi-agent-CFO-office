"""revenue_recognition_agent.py - Connect billings to recognized and deferred revenue."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_core as core
import o2c_policy as P


class RevenueRecognitionAgent(O2CAgent):
    name = "RevenueRecognitionAgent"
    purpose = "Tie billings to recognized and deferred revenue; flag cutoff and rollforward breaks."
    maker_owner = P.MAKER_OWNERS["RevenueRecognitionAgent"]
    checker_owner = P.CHECKER_OWNERS["RevenueRecognitionAgent"]
    input_tables = ["revenue", "deferred", "invoices", "contracts"]
    output_artifacts = ["cutoff_exceptions", "deferred_rollforward_breaks",
                        "recognized_vs_invoiced_bridge", "accounting_review_queue"]
    deterministic_checks_used = ["K_REVENUE_RECOGNITION_CUTOFF", "L_DEFERRED_REVENUE_ROLLFORWARD"]

    def analyze(self, ctx):
        period = ctx.period
        rr = core.calculate_revenue_recognition_rollforward(ctx.dfs, period)
        dr = core.calculate_deferred_revenue_rollforward(ctx.dfs, period)
        bridge = ctx.calc["bridge"]

        invoiced = bridge["billings_usd"]
        recognized = bridge["recognized_revenue_usd"]
        deferred_movement = round(invoiced - recognized, 2)

        review_queue = rr["cutoff_exception_count"] + dr["rollforward_break_count"]
        esc, actions = [], []
        if rr["cutoff_exception_count"]:
            esc.append(self.escalate("HIGH", f"{rr['cutoff_exception_count']} revenue cutoff "
                                      f"exceptions ({money(rr['cutoff_exceptions']['recognized_revenue_usd'].sum())})"))
            actions.append("Revenue Accounting: reverse/re-recognize out-of-period revenue.")
        if dr["rollforward_break_count"]:
            esc.append(self.escalate("HIGH", f"{dr['rollforward_break_count']} deferred revenue "
                                      f"rollforward breaks ({money(dr['rollforward_break_amount_usd'])})"))
            actions.append("Revenue Accounting: correct the deferred revenue rollforward.")

        headline = (f"{rr['cutoff_exception_count']} cutoff, {dr['rollforward_break_count']} "
                    f"rollforward breaks")
        narrative = (
            f"Revenue recognition for {period}: recognized {money(recognized)} against billings "
            f"{money(invoiced)} (net deferred movement {money(deferred_movement)}); deferred ending "
            f"{money(dr['deferred_ending_usd'])}. {rr['cutoff_exception_count']} cutoff exceptions "
            f"(recognized outside the service period or after contract end) and "
            f"{dr['rollforward_break_count']} deferred rollforward breaks require accounting review.")
        return {"headline": headline, "narrative": narrative,
                "recognized_revenue_usd": recognized, "invoiced_usd": invoiced,
                "deferred_ending_usd": dr["deferred_ending_usd"],
                "recognized_vs_invoiced_bridge": {"invoiced_usd": invoiced,
                    "recognized_usd": recognized, "deferred_movement_usd": deferred_movement},
                "cutoff_exceptions": df_records(rr["cutoff_exceptions"], ["revenue_schedule_id",
                    "invoice_id", "revenue_month", "before_service_start",
                    "after_contract_end_no_renew"]),
                "deferred_rollforward_breaks": df_records(dr["breaks"], ["period", "contract_id",
                    "closing_deferred_revenue", "expected_closing", "foot_diff"]),
                "accounting_review_queue_size": int(review_queue),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return RevenueRecognitionAgent().run(ctx)
