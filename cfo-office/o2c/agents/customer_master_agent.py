"""customer_master_agent.py - Validate customer master data quality."""

import base_agent
from base_agent import O2CAgent, df_records
import o2c_policy as P


class CustomerMasterAgent(O2CAgent):
    name = "CustomerMasterAgent"
    purpose = "Validate customer master data quality and build a remediation queue."
    maker_owner = P.MAKER_OWNERS["CustomerMasterAgent"]
    checker_owner = P.CHECKER_OWNERS["CustomerMasterAgent"]
    input_tables = ["customers", "contracts", "orders", "invoices"]
    output_artifacts = ["master_data_gaps", "credit_hold_with_orders", "remediation_queue"]
    deterministic_checks_used = ["N_CREDIT_HOLD_NEW_ORDER_BLOCK", "S8_NON_STANDARD_TERMS",
                                 "S9_STALE_CREDIT_REVIEW"]

    def analyze(self, ctx):
        cust = ctx.dfs["customers"]
        ctr = ctx.dfs["contracts"]
        orders = ctx.dfs["orders"]

        missing_tax = cust[cust["tax_profile"].fillna("").astype(str).str.len() == 0]
        missing_terms = cust[cust["payment_terms"].fillna("").astype(str).str.len() == 0]

        inactive = cust[cust["customer_status"].isin(["churned", "suspended"])]["customer_id"]
        active_ctr_cust = set(ctr[ctr["contract_status"] == "active"]["customer_id"])
        inactive_with_contract = inactive[inactive.isin(active_ctr_cust)]

        hold = cust[(cust["customer_status"] == "credit-hold") | (cust["credit_status"] == "hold")]
        active_ord_cust = set(orders[orders["order_status"] == "active"]["customer_id"])
        hold_with_orders = hold[hold["customer_id"].isin(active_ord_cust)]

        # currency mismatch: a contract booked in a currency other than the customer default
        cust_ccy = cust.set_index("customer_id")["default_currency"]
        ctr2 = ctr.copy()
        ctr2["default_ccy"] = ctr2["customer_id"].map(cust_ccy)
        ccy_mismatch = ctr2[ctr2["currency"] != ctr2["default_ccy"]]

        queue_size = (len(missing_tax) + len(missing_terms) + len(inactive_with_contract)
                      + len(hold_with_orders) + len(ccy_mismatch))
        esc, actions = [], []
        if len(hold_with_orders):
            esc.append(self.escalate("HIGH", f"{len(hold_with_orders)} credit-hold customers have "
                                      f"active orders"))
            actions.append("Credit: release or cancel active orders for credit-hold customers.")
        if len(inactive_with_contract):
            esc.append(self.escalate("MEDIUM", f"{len(inactive_with_contract)} churned/suspended "
                                      f"customers still have active contracts"))
        if len(missing_tax) or len(missing_terms):
            actions.append("Finance Ops: complete missing tax profile / payment terms on master data.")

        headline = f"{queue_size} master-data items to remediate"
        narrative = (
            f"Customer master review for {ctx.period}: {len(cust)} customers. "
            f"{len(missing_tax)} missing a tax profile, {len(missing_terms)} missing payment terms, "
            f"{len(inactive_with_contract)} inactive customers still hold an active contract, "
            f"{len(hold_with_orders)} credit-hold customers have active orders, and "
            f"{len(ccy_mismatch)} contracts are booked in a non-default currency.")
        return {"headline": headline, "narrative": narrative,
                "missing_tax_profile": int(len(missing_tax)),
                "missing_payment_terms": int(len(missing_terms)),
                "inactive_with_active_contract": df_records(inactive_with_contract.to_frame(),
                                                            ["customer_id"]),
                "credit_hold_with_orders": df_records(hold_with_orders,
                    ["customer_id", "customer_name", "credit_status"]),
                "currency_mismatch_contracts": df_records(ccy_mismatch,
                    ["contract_id", "customer_id", "currency", "default_ccy"]),
                "remediation_queue_size": int(queue_size),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return CustomerMasterAgent().run(ctx)
