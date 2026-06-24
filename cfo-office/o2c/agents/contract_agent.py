"""contract_agent.py - Validate contract terms and commercial logic."""

import base_agent
from base_agent import O2CAgent, df_records
import o2c_core as core
import o2c_policy as P
import pandas as pd


class ContractAgent(O2CAgent):
    name = "ContractAgent"
    purpose = "Validate contract terms, revenue recognition method, and renewal risk."
    maker_owner = P.MAKER_OWNERS["ContractAgent"]
    checker_owner = P.CHECKER_OWNERS["ContractAgent"]
    input_tables = ["contracts", "customers", "invoices"]
    output_artifacts = ["missing_billing_terms", "invalid_service_periods",
                        "non_standard_rev_rec", "renewal_risk"]
    deterministic_checks_used = ["F_PO_REQUIRED_CONTROL"]

    def analyze(self, ctx):
        ctr = ctx.dfs["contracts"].copy()
        asof = core.as_of_date(ctx.period)

        missing_terms = ctr[(ctr["billing_model"].fillna("").astype(str).str.len() == 0)
                            | (ctr["billing_frequency"].fillna("").astype(str).str.len() == 0)
                            | (ctr["payment_terms"].fillna("").astype(str).str.len() == 0)]
        invalid_periods = ctr[ctr["contract_end_date"] <= ctr["contract_start_date"]]
        non_standard = ctr[ctr["non_standard_terms_flag"] == 1]

        # renewal / expiration risk: active contracts ending within 90 days, no auto-renew
        horizon = asof + pd.Timedelta(days=90)
        renewal_risk = ctr[(ctr["contract_status"] == "active")
                           & (ctr["contract_end_date"] >= asof)
                           & (ctr["contract_end_date"] <= horizon)
                           & (ctr["auto_renew_flag"] == 0)]

        esc, actions = [], []
        if len(invalid_periods):
            esc.append(self.escalate("HIGH", f"{len(invalid_periods)} contracts have an invalid "
                                      f"service period (end on/before start)"))
        if len(renewal_risk):
            esc.append(self.escalate("MEDIUM", f"{len(renewal_risk)} active contracts "
                                      f"({base_agent.money(renewal_risk['arr_amount_usd'].sum())} ARR) "
                                      f"expire within 90 days without auto-renew"))
            actions.append("RevOps: open renewal motions for at-risk contracts.")
        if len(non_standard):
            actions.append(f"Legal/RevOps: review {len(non_standard)} contracts with non-standard terms.")

        headline = (f"{len(non_standard)} non-standard, {len(renewal_risk)} expiring, "
                    f"{len(invalid_periods)} invalid periods")
        narrative = (
            f"Contract review for {ctx.period}: {len(ctr)} contracts. "
            f"{len(missing_terms)} missing billing terms, {len(invalid_periods)} with an invalid "
            f"service period, {len(non_standard)} on non-standard terms, and {len(renewal_risk)} "
            f"active contracts expiring within 90 days without auto-renew (renewal risk).")
        return {"headline": headline, "narrative": narrative,
                "missing_billing_terms": int(len(missing_terms)),
                "invalid_service_periods": df_records(invalid_periods,
                    ["contract_id", "contract_start_date", "contract_end_date"]),
                "non_standard_rev_rec": int(len(non_standard)),
                "renewal_risk": df_records(renewal_risk, ["contract_id", "customer_id",
                                                          "contract_end_date", "arr_amount_usd"]),
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return ContractAgent().run(ctx)
