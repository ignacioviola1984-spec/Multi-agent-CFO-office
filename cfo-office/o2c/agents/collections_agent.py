"""collections_agent.py - Prioritize collections and forecast cash."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_core as core
import o2c_policy as P

TIER_MULT = {"high": 3.0, "medium": 2.0, "low": 1.0}


def score_collections_risk(open_items, customers):
    """Deterministic per-customer collections risk score.

    Higher when a customer has more overdue cash, older balances, and a worse
    risk tier. Returns customers ranked by risk_score descending. Exposed at
    module level so the score is unit-testable in isolation.
    """
    overdue = open_items[(open_items["days_overdue"] > 0) & (~open_items["is_disputed"])]
    if len(overdue) == 0:
        return overdue.assign(risk_score=[]).head(0)
    g = (overdue.groupby("customer_id")
         .agg(overdue_usd=("open_usd", "sum"), max_days=("days_overdue", "max"),
              n_overdue=("invoice_id", "count")).reset_index())
    tier = customers.set_index("customer_id")["risk_tier"]
    g["risk_tier"] = g["customer_id"].map(tier).fillna("medium")
    g["tier_mult"] = g["risk_tier"].map(TIER_MULT).fillna(1.0)
    g["risk_score"] = (g["overdue_usd"] * (1.0 + g["max_days"] / 365.0) * g["tier_mult"]).round(1)
    return g.sort_values("risk_score", ascending=False).reset_index(drop=True)


class CollectionsAgent(O2CAgent):
    name = "CollectionsAgent"
    purpose = "Prioritize collections, forecast cash, and rank customer risk."
    maker_owner = P.MAKER_OWNERS["CollectionsAgent"]
    checker_owner = P.CHECKER_OWNERS["CollectionsAgent"]
    input_tables = ["invoices", "cash_application", "credit_memos", "collections", "customers"]
    output_artifacts = ["ar_aging", "top_collection_priorities", "expected_cash",
                        "customer_risk_ranking", "broken_promises"]
    deterministic_checks_used = ["S2_HIGH_DSO", "S3_AGING_CONCENTRATION", "S4_BROKEN_PROMISE"]

    def analyze(self, ctx):
        period = ctx.period
        aging = ctx.calc["aging"]
        items = ctx.calc["open_items"]
        forecast = core.calculate_collections_forecast(ctx.dfs, period)
        dso = core.calculate_dso(ctx.dfs, period)

        ranking = score_collections_risk(items, ctx.dfs["customers"])
        top = ranking.head(10)

        esc, actions = [], []
        status = P.status_for_dso(dso["dso"])
        if status in ("URGENT", "CRITICAL"):
            esc.append(self.escalate("HIGH" if status == "URGENT" else "CRITICAL",
                       f"DSO {dso['dso']} days vs best-possible {dso['best_possible_dso']} "
                       f"(target < {P.DSO_WARNING_THRESHOLD:.0f})"))
        if forecast["broken_promise_count"]:
            esc.append(self.escalate("HIGH", f"{forecast['broken_promise_count']} broken promises "
                                      f"to pay ({money(forecast['broken_promise_amount_usd'])})"))
            actions.append("Collections: re-engage and escalate broken promises.")
        if len(top):
            actions.append(f"Collections: work the top {len(top)} risk accounts "
                           f"({money(top['overdue_usd'].sum())} overdue).")
        if aging["ar_90_plus_usd"] > 0:
            actions.append(f"Assess reserve adequacy on {money(aging['ar_90_plus_usd'])} of 90+ AR.")

        headline = (f"DSO {dso['dso']}d, {money(aging['overdue_ar_usd'])} overdue, "
                    f"expected 13w {money(forecast['expected_cash_13w_usd'])}")
        narrative = (
            f"Collections for {period}: open AR {money(aging['total_open_ar_usd'])}, of which "
            f"{money(aging['overdue_ar_usd'])} is overdue and {money(aging['ar_90_plus_usd'])} is 90+ days. "
            f"DSO is {dso['dso']} days against a best-possible {dso['best_possible_dso']}. "
            f"Expected cash (non-disputed) is {money(forecast['expected_cash_7d_usd'])} in 7d, "
            f"{money(forecast['expected_cash_30d_usd'])} in 30d, "
            f"{money(forecast['expected_cash_13w_usd'])} in 13 weeks. "
            f"{forecast['broken_promise_count']} promises to pay are broken.")
        return {"headline": headline, "narrative": narrative,
                "dso": dso["dso"], "best_possible_dso": dso["best_possible_dso"],
                "ar_aging": aging["by_bucket"].to_dict("records"),
                "open_ar_usd": aging["total_open_ar_usd"], "overdue_ar_usd": aging["overdue_ar_usd"],
                "expected_cash_7d_usd": forecast["expected_cash_7d_usd"],
                "expected_cash_30d_usd": forecast["expected_cash_30d_usd"],
                "expected_cash_13w_usd": forecast["expected_cash_13w_usd"],
                "top_collection_priorities": df_records(top, ["customer_id", "risk_tier",
                    "overdue_usd", "max_days", "n_overdue", "risk_score"]),
                "broken_promises": df_records(forecast["broken_promises"], ["invoice_id",
                    "customer_id", "promise_to_pay_date", "promised_amount"]),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return CollectionsAgent().run(ctx)
