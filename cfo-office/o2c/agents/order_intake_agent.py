"""order_intake_agent.py - Diagnose CRM -> contract -> order conversion."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_policy as P


class OrderIntakeAgent(O2CAgent):
    name = "OrderIntakeAgent"
    purpose = "Diagnose CRM-to-order conversion gaps and order blocks."
    maker_owner = P.MAKER_OWNERS["OrderIntakeAgent"]
    checker_owner = P.CHECKER_OWNERS["OrderIntakeAgent"]
    input_tables = ["opportunities", "contracts", "orders", "customers"]
    output_artifacts = ["closed_won_not_contracted", "contract_not_ordered", "order_blocks",
                        "intake_escalations"]
    deterministic_checks_used = ["A_CRM_CLOSED_WON_TO_CONTRACT", "B_CONTRACT_TO_ORDER",
                                 "C_ORDER_TO_BILLING_SCHEDULE"]

    def analyze(self, ctx):
        chain = ctx.calc["chain"]
        won = chain["closed_won"]
        not_contracted = won[~won["has_contract"]]
        not_ordered = chain["active_contracts"][~chain["active_contracts"]["has_order"]]
        not_billed = chain["active_orders"][~chain["active_orders"]["has_billing"]]

        orders = ctx.dfs["orders"]
        blocked = orders[orders["billing_block_flag"] == 1]
        cust = ctx.dfs["customers"]
        non_standard = cust[~cust["payment_terms"].isin(P.STANDARD_PAYMENT_TERMS)]

        esc, actions = [], []
        if len(not_contracted):
            esc.append(self.escalate("HIGH", f"{len(not_contracted)} closed-won opportunities "
                                      f"({money(not_contracted['amount_usd'].sum())}) have no contract"))
            actions.append("RevOps: convert closed-won opportunities to contracts this week.")
        if len(not_ordered):
            esc.append(self.escalate("HIGH", f"{len(not_ordered)} active contracts "
                                      f"({money(not_ordered['contract_value_usd'].sum())}) have no sales order"))
            actions.append("Provision sales orders for the unordered active contracts.")
        if len(not_billed):
            esc.append(self.escalate("MEDIUM", f"{len(not_billed)} active orders have no billing schedule"))
        if len(blocked):
            actions.append(f"Clear {len(blocked)} billing-blocked orders or document the reason.")

        headline = (f"{len(not_contracted)} not contracted, {len(not_ordered)} not ordered, "
                    f"{len(blocked)} order blocks")
        narrative = (
            f"Order intake for {ctx.period}: {len(won)} closed-won opportunities reviewed. "
            f"{len(not_contracted)} are not yet under contract and {len(not_ordered)} active "
            f"contracts have no sales order, which stalls billing downstream. "
            f"{len(blocked)} orders carry a billing block and {len(non_standard)} customers are on "
            f"non-standard payment terms.")
        return {"headline": headline, "narrative": narrative,
                "closed_won_not_contracted": df_records(not_contracted,
                    ["opportunity_id", "customer_id", "amount_usd"]),
                "contract_not_ordered": df_records(not_ordered, ["contract_id", "customer_id",
                                                                 "contract_value_usd"]),
                "order_blocks": df_records(blocked, ["order_id", "customer_id", "billing_block_reason"]),
                "non_standard_terms_customers": int(len(non_standard)),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return OrderIntakeAgent().run(ctx)
