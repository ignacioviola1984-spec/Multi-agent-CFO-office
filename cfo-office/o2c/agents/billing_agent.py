"""billing_agent.py - Ensure complete, accurate, and timely billing."""

import base_agent
from base_agent import O2CAgent, money, df_records
import o2c_core as core
import o2c_policy as P


class BillingAgent(O2CAgent):
    name = "BillingAgent"
    purpose = "Ensure billing is complete, accurate, and timely; quantify leakage."
    maker_owner = P.MAKER_OWNERS["BillingAgent"]
    checker_owner = P.CHECKER_OWNERS["BillingAgent"]
    input_tables = ["billing", "invoices", "customers"]
    output_artifacts = ["unbilled_revenue", "late_invoices", "invoice_mismatches",
                        "duplicate_invoices", "billing_exceptions_by_owner"]
    deterministic_checks_used = ["D_BILLING_COMPLETENESS", "E_INVOICE_ACCURACY",
                                 "F_PO_REQUIRED_CONTROL", "G_INVOICE_DUPLICATE_CONTROL",
                                 "S1_BILLING_TIMELINESS"]

    def analyze(self, ctx):
        period = ctx.period
        unb = core.calculate_unbilled_revenue(ctx.dfs, period)
        acc = core.calculate_invoice_accuracy(ctx.dfs, period)
        tim = core.calculate_billing_timeliness(ctx.dfs, period)
        comp = core.calculate_billing_completeness(ctx.dfs, period)

        inv = ctx.dfs["invoices"]
        cust = ctx.dfs["customers"][["customer_id", "po_required_flag"]]
        j = inv.merge(cust, on="customer_id", how="left")
        po_gaps = j[(j["po_required_flag"] == 1) & (j["po_number"].fillna("").astype(str).str.len() == 0)]

        dup = inv[inv.duplicated(subset=["customer_id", "order_id", "service_period_start",
                                         "service_period_end", "invoice_amount"], keep=False)]

        bill = ctx.dfs["billing"]
        blocked = bill[bill["billing_status"] == "blocked"]
        by_reason = (blocked.groupby("billing_exception_reason").size()
                     .reset_index(name="count").to_dict("records"))

        esc, actions = [], []
        if unb["unbilled_amount_usd"] > P.MATERIALITY_THRESHOLD_USD:
            esc.append(self.escalate("CRITICAL", f"{money(unb['unbilled_amount_usd'])} unbilled "
                                      f"revenue ({unb['unbilled_count']} lines): direct leakage"))
            actions.append("Billing: invoice the unbilled lines immediately (revenue leakage).")
        if acc["mismatch_count"]:
            esc.append(self.escalate("HIGH", f"{acc['mismatch_count']} invoices do not match the "
                                      f"scheduled amount ({money(acc['mismatch_amount_usd'])})"))
            actions.append("Billing: credit/rebill the mismatched invoices.")
        if len(dup):
            esc.append(self.escalate("HIGH", f"{len(dup)} invoices look like duplicates"))
            actions.append("Billing: void duplicate invoices.")
        if len(po_gaps):
            actions.append(f"Billing: obtain POs for {len(po_gaps)} invoices that require one.")

        headline = (f"{money(unb['unbilled_amount_usd'])} leakage, {acc['mismatch_count']} mismatches, "
                    f"{len(dup)} duplicates")
        narrative = (
            f"Billing review for {period}: completeness {comp['billing_completeness_pct']}%, "
            f"accuracy {acc['invoice_accuracy_pct']}%, timeliness {tim['billing_timeliness_pct']}%. "
            f"Unbilled (leakage) {money(unb['unbilled_amount_usd'])} across {unb['unbilled_count']} lines; "
            f"{acc['mismatch_count']} amount mismatches; {len(dup)} probable duplicates; "
            f"{tim['late_count']} late invoices; {len(po_gaps)} PO gaps.")
        return {"headline": headline, "narrative": narrative,
                "billing_completeness_pct": comp["billing_completeness_pct"],
                "invoice_accuracy_pct": acc["invoice_accuracy_pct"],
                "billing_timeliness_pct": tim["billing_timeliness_pct"],
                "unbilled_revenue_usd": unb["unbilled_amount_usd"],
                "revenue_leakage_usd": unb["unbilled_amount_usd"],
                "unbilled": df_records(unb["unbilled"], ["billing_schedule_id", "customer_id",
                                                         "scheduled_bill_amount_usd"]),
                "invoice_mismatches": df_records(acc["mismatches"], ["invoice_id", "scheduled_usd",
                                                                     "invoice_amount_usd", "diff_usd"]),
                "duplicate_invoices": df_records(dup, ["invoice_id", "customer_id", "invoice_amount"]),
                "po_gaps": int(len(po_gaps)), "late_invoices": int(tim["late_count"]),
                "billing_exceptions_by_owner": by_reason,
                "escalations": esc, "recommended_actions": actions}


def run(ctx):
    return BillingAgent().run(ctx)
