"""
o2c_metrics.py - The Order-to-Cash metrics framework.

Turns the deterministic core calculations into a governed metric set. Every
metric carries its business definition, the source tables, the accountable owner,
the policy threshold, and a status band (OK / REVIEW / URGENT / CRITICAL) so the
same number means the same thing to RevOps, Accounting, Treasury, and the CFO.

Metrics never recompute business logic; they read o2c_core and o2c_controls.
"""

from dataclasses import dataclass, asdict

import pandas as pd

try:
    import o2c_policy as P
    import o2c_core as core
    import o2c_controls as controls
except ImportError:                                    # pragma: no cover
    from . import o2c_policy as P
    from . import o2c_core as core
    from . import o2c_controls as controls


@dataclass
class Metric:
    metric_name: str
    value: float
    currency: str
    period: str
    source_tables: list
    business_definition: str
    owner: str
    threshold: str
    status: str

    def to_dict(self):
        d = asdict(self)
        d["source_tables"] = ",".join(self.source_tables)
        return d


def _band(value, ok, review, urgent, higher_is_better=True):
    """Map a value to OK/REVIEW/URGENT/CRITICAL against three cut points."""
    if higher_is_better:
        if value >= ok:
            return "OK"
        if value >= review:
            return "REVIEW"
        if value >= urgent:
            return "URGENT"
        return "CRITICAL"
    else:
        if value <= ok:
            return "OK"
        if value <= review:
            return "REVIEW"
        if value <= urgent:
            return "URGENT"
        return "CRITICAL"


def _amount_status(value, materiality=P.MATERIALITY_THRESHOLD_USD):
    if value <= 0:
        return "OK"
    if value <= materiality:
        return "REVIEW"
    if value <= materiality * 5:
        return "URGENT"
    return "CRITICAL"


def build_metrics(dfs, period=P.DEFAULT_PERIOD, controls_results=None, summary=None):
    """Compute the full O2C metric set for the period. Returns list[Metric]."""
    summary = summary or core.build_executive_o2c_summary(dfs, period)
    aging = core.calculate_ar_aging(dfs, period)
    bridge = core.build_bookings_billings_revenue_cash_bridge(dfs, period)
    comp = core.calculate_billing_completeness(dfs, period)
    cashapp = core.calculate_cash_application_status(dfs, period)
    if controls_results is None:
        controls_results = controls.run_all_controls(dfs, period)
    csum = controls.controls_summary(controls_results)

    buckets = {r["aging_bucket"]: r["open_ar_usd"] for r in aging["by_bucket"].to_dict("records")}
    orders = dfs["orders"]
    order_backlog = round(float(orders[orders["order_status"] == "active"]["order_amount_usd"].sum()), 2)

    # collection effectiveness index (proxy): collected / (collected + still-overdue)
    cash = summary["cash_collected_usd"]
    overdue = summary["overdue_ar_usd"]
    cei = round(cash / (cash + overdue) * 100.0, 1) if (cash + overdue) else 100.0

    O = P.METRIC_OWNERS
    m = []

    def add(name, value, ccy, tables, definition, owner, threshold, status):
        m.append(Metric(name, value, ccy, period, tables, definition, owner, threshold, status))

    # --- bookings / orders / billings ---
    add("bookings_amount", bridge["bookings_usd"], "USD", ["contracts"],
        "Total contract value signed in the period.", O["bookings"], "informational", "OK")
    add("contracted_arr", bridge["contracted_arr_usd"], "USD", ["contracts"],
        "Annual recurring revenue contracted in the period.", O["bookings"], "informational", "OK")
    add("order_amount", order_backlog, "USD", ["orders"],
        "Open active sales-order value (order book).", O["bookings"], "informational", "OK")
    add("scheduled_billings", comp["scheduled_due_usd"], "USD", ["billing"],
        "Scheduled, due, billable amount for the period.", O["billing"], "informational", "OK")
    add("actual_billings", bridge["billings_usd"], "USD", ["invoices"],
        "Amount invoiced in the period.", O["billing"], "informational", "OK")
    add("recognized_revenue", bridge["recognized_revenue_usd"], "USD", ["revenue"],
        "Revenue recognized in the period.", O["revenue"], "informational", "OK")
    add("deferred_revenue_ending", summary["deferred_revenue_ending_usd"], "USD", ["deferred"],
        "Deferred revenue balance at period end.", O["revenue"], "informational", "OK")

    # --- AR / aging ---
    add("open_ar", summary["open_ar_usd"], "USD", ["invoices", "cash_application", "credit_memos"],
        "Total open accounts receivable.", O["ar"], "informational", "OK")
    add("current_ar", summary["current_ar_usd"], "USD", ["invoices"],
        "AR not yet past due.", O["ar"], "informational", "OK")
    add("overdue_ar", summary["overdue_ar_usd"], "USD", ["invoices"],
        "AR past its due date.", O["ar"], "informational",
        _amount_status(summary["overdue_ar_usd"]))
    add("ar_30", buckets.get("1-30", 0.0), "USD", ["invoices"], "AR 1-30 days past due.",
        O["ar"], "informational", "OK")
    add("ar_60", buckets.get("31-60", 0.0), "USD", ["invoices"], "AR 31-60 days past due.",
        O["ar"], "informational", "REVIEW" if buckets.get("31-60", 0) else "OK")
    add("ar_90", buckets.get("61-90", 0.0), "USD", ["invoices"], "AR 61-90 days past due.",
        O["ar"], "informational", "URGENT" if buckets.get("61-90", 0) else "OK")
    add("ar_120_plus", round(buckets.get("91-120", 0.0) + buckets.get("120+", 0.0), 2), "USD",
        ["invoices"], "AR 90+ days past due (91-120 plus 120+).", O["ar"], "informational",
        _amount_status(buckets.get("91-120", 0.0) + buckets.get("120+", 0.0)))

    # --- DSO / efficiency ---
    add("dso", summary["dso"], "days", ["invoices", "cash_application"],
        "Days sales outstanding (trailing-3-month basis).", O["ar"],
        f"warn {P.DSO_WARNING_THRESHOLD} / urgent {P.DSO_URGENT_THRESHOLD} / crit {P.DSO_CRITICAL_THRESHOLD}",
        P.status_for_dso(summary["dso"]))
    add("best_possible_dso", summary["best_possible_dso"], "days", ["invoices"],
        "DSO if only current AR were outstanding.", O["ar"], "informational", "OK")
    add("collection_effectiveness_index", cei, "%", ["invoices", "cash_application"],
        "Collected / (collected + still-overdue), period proxy.", O["ar"],
        ">= 80% good", _band(cei, 80, 70, 60))

    # --- billing quality ---
    add("billing_completeness_pct", summary["billing_completeness_pct"], "%", ["billing", "invoices"],
        "Invoiced vs scheduled-and-due billable amount.", O["billing"], ">= 99%",
        _band(summary["billing_completeness_pct"], 99, 97, 95))
    add("billing_timeliness_pct", summary["billing_timeliness_pct"], "%", ["billing", "invoices"],
        "Invoices issued within the allowed delay.", O["billing"],
        f">= {P.BILLING_TIMELINESS_WARNING_PCT}%",
        _band(summary["billing_timeliness_pct"], 95, 90, 85))
    add("invoice_accuracy_pct", summary["invoice_accuracy_pct"], "%", ["billing", "invoices"],
        "Invoices matching the scheduled bill amount.", O["billing"], ">= 99%",
        _band(summary["invoice_accuracy_pct"], 99, 97, 95))
    add("unbilled_revenue_amount", summary["unbilled_revenue_usd"], "USD", ["billing", "invoices"],
        "Scheduled billable amount not yet invoiced.", O["billing"],
        f"<= {P.MATERIALITY_THRESHOLD_USD:.0f} USD", _amount_status(summary["unbilled_revenue_usd"]))
    add("revenue_leakage_estimate", summary["revenue_leakage_usd"], "USD", ["billing", "invoices"],
        "Estimated revenue lost to unbilled work.", O["billing"],
        f"<= {P.MATERIALITY_THRESHOLD_USD:.0f} USD", _amount_status(summary["revenue_leakage_usd"]))

    # --- disputes / cash application ---
    add("disputed_ar_amount", summary["disputed_ar_usd"], "USD", ["disputes", "invoices"],
        "Open AR under dispute (cash-blocked).", O["credit"], "informational",
        _amount_status(summary["disputed_ar_usd"]))
    add("disputed_ar_pct", summary["disputed_ar_pct"], "%", ["disputes", "invoices"],
        "Disputed AR as a share of open AR.", O["credit"],
        f"<= {P.DISPUTED_AR_WARNING_PCT}%", _band(summary["disputed_ar_pct"], 8, 12, 20, higher_is_better=False))
    add("unapplied_cash_amount", summary["unapplied_cash_usd"], "USD", ["cash_application", "bank_receipts"],
        "Cash received but not applied to AR.", O["cash"], "informational",
        _amount_status(summary["unapplied_cash_usd"]))
    add("cash_application_rate", summary["cash_application_rate_pct"], "%", ["cash_application", "bank_receipts"],
        "Cash applied vs cash received.", O["cash"], f">= {P.MINIMUM_CASH_APPLICATION_RATE}%",
        _band(summary["cash_application_rate_pct"], P.MINIMUM_CASH_APPLICATION_RATE, 90, 85))

    # --- credit ---
    add("credit_hold_exposure", summary["credit_hold_exposure_usd"], "USD", ["credit_limits"],
        "Exposure on customers flagged credit-hold.", O["credit"], "informational",
        _amount_status(summary["credit_hold_exposure_usd"]))
    add("credit_limit_breach_amount", summary["credit_breach_amount_usd"], "USD", ["credit_limits", "invoices"],
        "Exposure above approved credit limits.", O["credit"], "0 USD (hard control)",
        "CRITICAL" if summary["credit_breach_amount_usd"] > 0 else "OK")

    # --- collections / forecast ---
    add("broken_promise_amount", summary["broken_promise_amount_usd"], "USD", ["collections", "invoices"],
        "Promised amounts past due and unpaid.", O["ar"], "informational",
        _amount_status(summary["broken_promise_amount_usd"]))
    add("expected_cash_7d", summary["expected_cash_7d_usd"], "USD", ["invoices", "collections"],
        "Expected collections in 7 days (non-disputed).", O["cash"], "informational", "OK")
    add("expected_cash_30d", summary["expected_cash_30d_usd"], "USD", ["invoices", "collections"],
        "Expected collections in 30 days (non-disputed).", O["cash"], "informational", "OK")
    add("expected_cash_13w", summary["expected_cash_13w_usd"], "USD", ["invoices", "collections"],
        "Expected collections in 13 weeks (non-disputed).", O["cash"], "informational", "OK")

    # --- control health ---
    add("o2c_control_pass_rate", csum["control_pass_rate_pct"], "%", ["all"],
        "Share of O2C controls passing.", O["controls"], ">= 95%",
        _band(csum["control_pass_rate_pct"], 95, 80, 60))
    add("hard_control_failure_count", csum["hard_failures"], "count", ["all"],
        "Number of hard controls failing (blocks reporting).", O["controls"], "0",
        "CRITICAL" if csum["hard_failures"] else "OK")
    add("soft_warning_count", csum["soft_warnings"], "count", ["all"],
        "Number of soft controls in warning.", O["controls"], "informational",
        "REVIEW" if csum["soft_warnings"] else "OK")

    return m


def metrics_to_dataframe(metrics):
    return pd.DataFrame([m.to_dict() for m in metrics])


if __name__ == "__main__":
    dfs = core.load()
    mts = build_metrics(dfs)
    df = metrics_to_dataframe(mts)
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(df[["metric_name", "value", "currency", "owner", "status"]].to_string(index=False))
    print(f"\n{len(mts)} metrics.")
