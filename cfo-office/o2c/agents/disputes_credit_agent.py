"""disputes_credit_agent.py - Separate true collection risk from disputed/blocked cash."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_core as core
import o2c_data_loader as loader
import o2c_policy as P


class DisputesCreditAgent(O2CAgent):
    name = "DisputesCreditAgent"
    purpose = "Separate disputed/blocked cash from collectible AR; manage credit risk."
    maker_owner = P.MAKER_OWNERS["DisputesCreditAgent"]
    checker_owner = P.CHECKER_OWNERS["DisputesCreditAgent"]
    input_tables = ["disputes", "invoices", "credit_limits", "customers", "orders"]
    output_artifacts = ["disputed_ar_by_reason", "cash_blocked", "owner_routing",
                        "credit_breaches", "credit_hold_violations", "resolution_priority"]
    deterministic_checks_used = ["M_CREDIT_LIMIT_BREACH", "N_CREDIT_HOLD_NEW_ORDER_BLOCK",
                                 "O_DISPUTE_COLLECTION_BLOCK", "S6_HIGH_DISPUTE_RATE"]

    def analyze(self, ctx):
        period = ctx.period
        disp = ctx.dfs["disputes"].copy()
        disp["disputed_amount_usd"] = loader.to_usd(disp["disputed_amount"], disp["currency"])
        open_disp = disp[disp["dispute_status"].isin(["open", "escalated"])]
        blocked = disp[disp["cash_blocked_flag"] == 1]

        by_reason = (open_disp.groupby("reason_code")["disputed_amount_usd"].sum()
                     .round(2).sort_values(ascending=False).reset_index().to_dict("records"))
        by_owner = (open_disp.groupby("owner_team")["disputed_amount_usd"].sum()
                    .round(2).sort_values(ascending=False).reset_index().to_dict("records"))

        credit = core.calculate_credit_exposure(ctx.dfs, period)

        orders = ctx.dfs["orders"]
        cust = ctx.dfs["customers"][["customer_id", "customer_status", "credit_status"]]
        oj = orders.merge(cust, on="customer_id", how="left")
        hold_orders = oj[(oj["order_status"] == "active")
                         & ((oj["credit_status"] == "hold") | (oj["customer_status"] == "credit-hold"))]

        resolution = open_disp.sort_values("disputed_amount_usd", ascending=False).head(10)

        esc, actions = [], []
        if credit["breach_count"]:
            esc.append(self.escalate("CRITICAL", f"{credit['breach_count']} customers over credit "
                                      f"limit ({money(credit['breach_amount_usd'])} above approved)"))
            actions.append("Credit: place breaching accounts on hold or document an override.")
        if len(hold_orders):
            esc.append(self.escalate("HIGH", f"{len(hold_orders)} active orders for credit-hold "
                                      f"customers"))
            actions.append("Credit: block/cancel orders for credit-hold customers.")
        if len(blocked):
            actions.append(f"Route {len(blocked)} cash-blocked disputes ({money(blocked['disputed_amount_usd'].sum())}) "
                           f"to the owning team and exclude from cash targets.")

        headline = (f"{credit['breach_count']} credit breaches, {len(hold_orders)} hold violations, "
                    f"{money(blocked['disputed_amount_usd'].sum())} cash blocked")
        narrative = (
            f"Disputes & credit for {period}: {len(open_disp)} open disputes "
            f"({money(open_disp['disputed_amount_usd'].sum())}); {money(blocked['disputed_amount_usd'].sum())} "
            f"of cash is blocked and routed out of collections. {credit['breach_count']} customers exceed "
            f"their credit limit ({money(credit['breach_amount_usd'])} over) and {len(hold_orders)} active "
            f"orders sit on credit-hold customers. Top dispute reason: "
            f"{by_reason[0]['reason_code'] if by_reason else 'n/a'}.")
        return {"headline": headline, "narrative": narrative,
                "disputed_ar_by_reason": by_reason, "owner_routing": by_owner,
                "cash_blocked_usd": round(float(blocked["disputed_amount_usd"].sum()), 2),
                "cash_blocked_count": int(len(blocked)),
                "credit_breaches": df_records(credit["breaches"], ["customer_id", "customer_name",
                    "credit_limit_usd", "current_exposure_amount_usd", "over_limit_usd"]),
                "credit_hold_violations": df_records(hold_orders, ["order_id", "customer_id",
                    "credit_status"]),
                "resolution_priority": df_records(resolution, ["dispute_id", "customer_id",
                    "reason_code", "owner_team", "disputed_amount_usd"]),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return DisputesCreditAgent().run(ctx)
