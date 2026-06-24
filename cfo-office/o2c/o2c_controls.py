"""
o2c_controls.py - Deterministic Order-to-Cash controls.

Fifteen HARD controls (a failure blocks downstream reporting) and ten SOFT
controls (warnings that route work but do not block). Each control re-derives
its answer from the source records via o2c_core and returns a ControlResult with
the failing records, the failing amount, the owner/checker, and a recommended
action, so every result is explainable and traceable.

A control NEVER invents a number. It re-performs a calculation and compares it to
a policy threshold (o2c_policy). The orchestrator turns HARD failures into a hard
gate on reporting.
"""

from dataclasses import dataclass, field, asdict

import pandas as pd

try:
    import o2c_policy as P
    import o2c_core as core
    import o2c_data_loader as loader
except ImportError:                                    # pragma: no cover
    from . import o2c_policy as P
    from . import o2c_core as core
    from . import o2c_data_loader as loader


@dataclass
class ControlResult:
    control_id: str
    control_name: str
    severity: str                 # HARD | SOFT
    status: str                   # PASS | FAIL | WARNING
    owner: str
    checker: str
    description: str
    failing_record_count: int = 0
    failing_amount_usd: float = 0.0
    source_tables: list = field(default_factory=list)
    exception_details: list = field(default_factory=list)
    recommended_action: str = ""
    blocks_reporting: bool = False

    def to_dict(self):
        d = asdict(self)
        d["exception_details"] = "; ".join(str(x) for x in self.exception_details[:10])
        d["source_tables"] = ",".join(self.source_tables)
        return d


def _ids(series, n=15):
    return list(series.astype(str).head(n))


# ==========================================================================
# HARD controls (a failure blocks reporting)
# ==========================================================================
def ctl_crm_closed_won_to_contract(dfs, period):
    chain = core.build_opportunity_to_cash_chain(dfs, period)
    won = chain["closed_won"]
    bad = won[~won["has_contract"]]
    fail = len(bad) > 0
    return ControlResult(
        "A_CRM_CLOSED_WON_TO_CONTRACT", "Closed-won opportunities tie to a contract",
        "HARD", "FAIL" if fail else "PASS", "Revenue Operations", "RevOps Lead",
        "Every closed-won opportunity must convert to a contract or have a documented exception.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["opportunities", "contracts"],
        exception_details=_ids(bad["opportunity_id"]),
        recommended_action="RevOps to create the missing contracts or close the opportunities.",
        blocks_reporting=fail)


def ctl_contract_to_order(dfs, period):
    chain = core.build_opportunity_to_cash_chain(dfs, period)
    bad = chain["active_contracts"][~chain["active_contracts"]["has_order"]]
    fail = len(bad) > 0
    return ControlResult(
        "B_CONTRACT_TO_ORDER", "Active contracts tie to a sales order",
        "HARD", "FAIL" if fail else "PASS", "Revenue Operations", "Revenue Operations Manager",
        "Every active contract must convert to a sales order.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["contract_value_usd"].sum()), 2) if fail else 0.0,
        source_tables=["contracts", "orders"], exception_details=_ids(bad["contract_id"]),
        recommended_action="Provision the missing sales orders for the active contracts.",
        blocks_reporting=fail)


def ctl_order_to_billing_schedule(dfs, period):
    chain = core.build_opportunity_to_cash_chain(dfs, period)
    bad = chain["active_orders"][~chain["active_orders"]["has_billing"]]
    fail = len(bad) > 0
    return ControlResult(
        "C_ORDER_TO_BILLING_SCHEDULE", "Active orders tie to a billing schedule",
        "HARD", "FAIL" if fail else "PASS", "Billing Operations", "Billing Manager",
        "Every active sales order must have a billing schedule so it gets invoiced.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["order_amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["orders", "billing"], exception_details=_ids(bad["order_id"]),
        recommended_action="Billing to build schedules for the unscheduled orders.",
        blocks_reporting=fail)


def ctl_billing_completeness(dfs, period):
    unb = core.calculate_unbilled_revenue(dfs, period)
    fail = unb["unbilled_amount_usd"] > P.MATERIALITY_THRESHOLD_USD
    return ControlResult(
        "D_BILLING_COMPLETENESS", "Scheduled billable amounts are invoiced",
        "HARD", "FAIL" if fail else "PASS", "Billing Operations", "Billing Manager",
        "Scheduled, due, billable amounts must be invoiced or carry a valid billing block.",
        failing_record_count=unb["unbilled_count"], failing_amount_usd=unb["unbilled_amount_usd"],
        source_tables=["billing", "invoices"], exception_details=_ids(unb["unbilled"]["billing_schedule_id"]),
        recommended_action="Invoice the unbilled lines; this is direct revenue leakage.",
        blocks_reporting=fail)


def ctl_invoice_accuracy(dfs, period):
    acc = core.calculate_invoice_accuracy(dfs, period)
    fail = acc["mismatch_count"] > 0
    return ControlResult(
        "E_INVOICE_ACCURACY", "Invoice amount matches the scheduled bill amount",
        "HARD", "FAIL" if fail else "PASS", "Billing Operations", "Billing Manager",
        "Invoice amount must match the scheduled bill amount within tolerance.",
        failing_record_count=acc["mismatch_count"], failing_amount_usd=acc["mismatch_amount_usd"],
        source_tables=["billing", "invoices"], exception_details=_ids(acc["mismatches"]["invoice_id"]),
        recommended_action="Re-bill or credit/rebill the mismatched invoices.",
        blocks_reporting=fail)


def ctl_po_required(dfs, period):
    inv = dfs["invoices"]
    cust = dfs["customers"][["customer_id", "po_required_flag"]]
    j = inv.merge(cust, on="customer_id", how="left")
    bad = j[(j["po_required_flag"] == 1) & (j["po_number"].fillna("").astype(str).str.len() == 0)]
    fail = len(bad) > 0
    return ControlResult(
        "F_PO_REQUIRED_CONTROL", "PO present where the customer requires one",
        "HARD", "FAIL" if fail else "PASS", "Billing Operations", "Billing Manager",
        "Customers that require a PO must have a PO number on the invoice.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["total_invoice_amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["invoices", "customers"], exception_details=_ids(bad["invoice_id"]),
        recommended_action="Obtain and attach the PO before the invoice is collectible.",
        blocks_reporting=fail)


def ctl_invoice_duplicate(dfs, period):
    inv = dfs["invoices"].copy()
    key = ["customer_id", "order_id", "service_period_start", "service_period_end", "invoice_amount"]
    dup_mask = inv.duplicated(subset=key, keep=False)
    bad = inv[dup_mask]
    fail = len(bad) > 0
    return ControlResult(
        "G_INVOICE_DUPLICATE_CONTROL", "No duplicate invoices",
        "HARD", "FAIL" if fail else "PASS", "Billing Operations", "Billing Manager",
        "No duplicate invoice for the same customer / order / service period / amount.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["total_invoice_amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["invoices"], exception_details=_ids(bad["invoice_id"]),
        recommended_action="Void the duplicate invoices and notify the affected customers.",
        blocks_reporting=fail)


def ctl_ar_subledger_completeness(dfs, period):
    items = core.calculate_invoice_open_amounts(dfs, period)
    by_open = round(float(items[items["open_usd"] > 1.0]["open_usd"].sum()), 2)         # control balance
    by_status = round(float(items[items["invoice_status"] != "paid"]["open_usd"].sum()), 2)  # subledger
    diff = round(by_open - by_status, 2)
    bad = items[(items["open_usd"] > 1.0) & (items["invoice_status"] == "paid")]
    fail = abs(diff) > P.AR_TO_GL_TOLERANCE_USD
    return ControlResult(
        "H_AR_SUBLEDGER_COMPLETENESS", "AR subledger ties to the control balance",
        "HARD", "FAIL" if fail else "PASS", "Accounting", "Controller / Internal Controls",
        "Invoice totals minus credits and applications must tie to open AR by status.",
        failing_record_count=int(len(bad)), failing_amount_usd=round(abs(diff), 2),
        source_tables=["invoices", "cash_application", "credit_memos"],
        exception_details=_ids(bad["invoice_id"]),
        recommended_action="Reconcile invoice status to the cash/credit transactions.",
        blocks_reporting=fail)


def ctl_cash_receipt_to_bank(dfs, period):
    pmt = dfs["payments"].copy()
    receipt_ids = set(dfs["bank_receipts"]["bank_receipt_id"])
    has_receipt = ((pmt["bank_receipt_id"].fillna("").astype(str).str.len() > 0)
                   & (pmt["bank_receipt_id"].isin(receipt_ids)))
    bad = pmt[~has_receipt].copy()
    bad["payment_amount_usd"] = loader.to_usd(bad["payment_amount"], bad["currency"])
    fail = len(bad) > 0
    return ControlResult(
        "I_CASH_RECEIPT_TO_BANK", "Payments tie to a bank receipt",
        "HARD", "FAIL" if fail else "PASS", "Cash Application", "Treasury / AR Manager",
        "Every recorded payment must tie to a bank receipt (proof of cash).",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["payment_amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["payments", "bank_receipts"], exception_details=_ids(bad["payment_id"]),
        recommended_action="Match the payments to the bank statement or reverse them.",
        blocks_reporting=fail)


def ctl_cash_application_completeness(dfs, period):
    ca = dfs["cash_application"]
    bad = ca[(ca["application_status"] == "unapplied")
             & (ca["unapplied_reason"].fillna("").astype(str).str.len() == 0)]
    bad = bad.merge(dfs["payments"][["payment_id", "payment_amount", "currency"]],
                    on="payment_id", how="left")
    bad["amt_usd"] = loader.to_usd(bad["payment_amount"].fillna(0.0), bad["currency"].fillna("USD"))
    fail = len(bad) > 0
    return ControlResult(
        "J_CASH_APPLICATION_COMPLETENESS", "Receipts are applied or documented",
        "HARD", "FAIL" if fail else "PASS", "Cash Application", "Treasury / AR Manager",
        "Cash must be applied to AR or carry a valid unapplied reason.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["amt_usd"].sum()), 2) if fail else 0.0,
        source_tables=["cash_application", "bank_receipts"],
        exception_details=_ids(bad["cash_application_id"]),
        recommended_action="Apply the cash or document the unapplied reason.",
        blocks_reporting=fail)


def ctl_revenue_recognition_cutoff(dfs, period):
    rr = core.calculate_revenue_recognition_rollforward(dfs, period)
    fail = rr["cutoff_exception_count"] > 0
    amt = round(float(rr["cutoff_exceptions"]["recognized_revenue_usd"].sum()), 2) if fail else 0.0
    return ControlResult(
        "K_REVENUE_RECOGNITION_CUTOFF", "Revenue recognized within service / contract terms",
        "HARD", "FAIL" if fail else "PASS", "Revenue Accounting", "Revenue Accounting Manager",
        "Recognized revenue must fall within the service period and contract term.",
        failing_record_count=rr["cutoff_exception_count"], failing_amount_usd=amt,
        source_tables=["revenue", "invoices", "contracts"],
        exception_details=_ids(rr["cutoff_exceptions"]["revenue_schedule_id"]),
        recommended_action="Reverse and re-recognize the out-of-period revenue.",
        blocks_reporting=fail)


def ctl_deferred_revenue_rollforward(dfs, period):
    dr = core.calculate_deferred_revenue_rollforward(dfs, period)
    fail = dr["rollforward_break_count"] > 0
    return ControlResult(
        "L_DEFERRED_REVENUE_ROLLFORWARD", "Deferred revenue rollforward foots",
        "HARD", "FAIL" if fail else "PASS", "Revenue Accounting", "Revenue Accounting Manager",
        "opening + billings - recognized + adjustments + fx must equal closing deferred.",
        failing_record_count=dr["rollforward_break_count"],
        failing_amount_usd=dr["rollforward_break_amount_usd"],
        source_tables=["deferred", "revenue"], exception_details=_ids(dr["breaks"]["contract_id"]),
        recommended_action="Investigate and correct the deferred revenue rollforward breaks.",
        blocks_reporting=fail)


def ctl_credit_limit_breach(dfs, period):
    ce = core.calculate_credit_exposure(dfs, period)
    fail = ce["breach_count"] > 0
    return ControlResult(
        "M_CREDIT_LIMIT_BREACH", "Exposure within the approved credit limit",
        "HARD", "FAIL" if fail else "PASS", "Credit & Commercial Finance",
        "Credit & Commercial Finance Manager",
        "Customer exposure must not exceed the approved credit limit unless approved.",
        failing_record_count=ce["breach_count"], failing_amount_usd=ce["breach_amount_usd"],
        source_tables=["credit_limits", "invoices"], exception_details=_ids(ce["breaches"]["customer_id"]),
        recommended_action="Place on hold or obtain a documented credit-limit override.",
        blocks_reporting=fail)


def ctl_credit_hold_new_order(dfs, period):
    orders = dfs["orders"]
    cust = dfs["customers"][["customer_id", "customer_status", "credit_status"]]
    j = orders.merge(cust, on="customer_id", how="left")
    bad = j[(j["order_status"] == "active")
            & ((j["credit_status"] == "hold") | (j["customer_status"] == "credit-hold"))]
    bad = bad.copy()
    bad["order_amount_usd"] = loader.to_usd(bad["order_amount"], bad["currency"])
    fail = len(bad) > 0
    return ControlResult(
        "N_CREDIT_HOLD_NEW_ORDER_BLOCK", "No active orders for customers on credit hold",
        "HARD", "FAIL" if fail else "PASS", "Credit & Commercial Finance",
        "Credit & Commercial Finance Manager",
        "Customers on credit hold must not have active (new) sales orders.",
        failing_record_count=int(len(bad)),
        failing_amount_usd=round(float(bad["order_amount_usd"].sum()), 2) if fail else 0.0,
        source_tables=["orders", "customers"], exception_details=_ids(bad["order_id"]),
        recommended_action="Block or cancel the orders pending credit release.",
        blocks_reporting=fail)


def ctl_dispute_collection_block(dfs, period):
    """Segregation: disputed (cash-blocked) invoices must be out of the normal
    collections forecast and each must carry an owner team."""
    forecast = core.calculate_collections_forecast(dfs, period)
    items = core.calculate_ar_open_items(dfs, period)
    workable_ids = set(items[~items["is_disputed"]]["invoice_id"])
    disp = dfs["disputes"]
    blocked = disp[disp["cash_blocked_flag"] == 1]
    leaked = blocked[blocked["invoice_id"].isin(workable_ids)]                 # should be empty
    no_owner = blocked[blocked["owner_team"].fillna("").astype(str).str.len() == 0]
    fail = (len(leaked) > 0) or (len(no_owner) > 0)
    return ControlResult(
        "O_DISPUTE_COLLECTION_BLOCK", "Disputed cash is routed out of collections",
        "HARD", "FAIL" if fail else "PASS", "Credit & Disputes",
        "Credit & Commercial Finance Manager",
        "Disputed (cash-blocked) invoices must be excluded from the cash forecast and owned.",
        failing_record_count=int(len(leaked) + len(no_owner)),
        failing_amount_usd=forecast["disputed_excluded_usd"],
        source_tables=["disputes", "invoices"], exception_details=_ids(blocked["dispute_id"]),
        recommended_action="Route disputed AR to the owner team; exclude from cash targets.",
        blocks_reporting=fail)


# ==========================================================================
# SOFT controls (warnings; do not block reporting)
# ==========================================================================
def _soft(cid, name, owner, checker, desc, warn, count, amount, tables, action, details=None):
    return ControlResult(cid, name, "SOFT", "WARNING" if warn else "PASS", owner, checker, desc,
                         failing_record_count=int(count), failing_amount_usd=round(float(amount), 2),
                         source_tables=tables, exception_details=details or [],
                         recommended_action=action, blocks_reporting=False)


def ctl_soft_billing_timeliness(dfs, period):
    t = core.calculate_billing_timeliness(dfs, period)
    warn = t["billing_timeliness_pct"] < P.BILLING_TIMELINESS_WARNING_PCT
    return _soft("S1_BILLING_TIMELINESS", "Billing timeliness", "Billing Operations", "Billing Manager",
                 f"On-time billing {t['billing_timeliness_pct']}% vs {P.BILLING_TIMELINESS_WARNING_PCT}% target.",
                 warn, t["late_count"], 0.0, ["billing", "invoices"],
                 "Accelerate the late invoices to protect DSO.", _ids(t["late"]["invoice_id"]))


def ctl_soft_high_dso(dfs, period):
    d = core.calculate_dso(dfs, period)
    warn = d["dso"] >= P.DSO_WARNING_THRESHOLD
    status = P.status_for_dso(d["dso"])
    r = _soft("S2_HIGH_DSO", "DSO above target", "Collections", "Collections Manager",
              f"DSO {d['dso']} days (best possible {d['best_possible_dso']}).",
              warn, 0, 0.0, ["invoices", "cash_application"],
              "Prioritize collections on the oldest, largest balances.")
    r.status = status if warn else "PASS"
    return r


def ctl_soft_aging_concentration(dfs, period):
    aging = core.calculate_ar_aging(dfs, period)
    total = aging["total_open_ar_usd"] or 1.0
    pct = aging["ar_90_plus_usd"] / total * 100.0
    warn = pct > P.AGING_CONCENTRATION_WARNING_PCT
    return _soft("S3_AGING_CONCENTRATION", "Aged AR concentration", "Collections", "Collections Manager",
                 f"{round(pct, 1)}% of open AR is 90+ days (limit {P.AGING_CONCENTRATION_WARNING_PCT}%).",
                 warn, 0, aging["ar_90_plus_usd"], ["invoices"],
                 "Escalate the 90+ balances; assess reserve adequacy.")


def ctl_soft_broken_promise(dfs, period):
    f = core.calculate_collections_forecast(dfs, period)
    warn = f["broken_promise_count"] > 0
    return _soft("S4_BROKEN_PROMISE", "Broken promises to pay", "Collections", "Collections Manager",
                 f"{f['broken_promise_count']} promises to pay are past due and unpaid.",
                 warn, f["broken_promise_count"], f["broken_promise_amount_usd"],
                 ["collections", "invoices"], "Re-engage and escalate broken promises.",
                 _ids(f["broken_promises"]["invoice_id"]))


def ctl_soft_high_unapplied_cash(dfs, period):
    u = core.calculate_unapplied_cash(dfs, period)
    capp = core.calculate_cash_application_status(dfs, period)
    received = capp["received_usd"] or 1.0
    pct = u["unapplied_cash_usd"] / received * 100.0
    warn = pct > P.HIGH_UNAPPLIED_CASH_WARNING_PCT
    return _soft("S5_HIGH_UNAPPLIED_CASH", "Unapplied cash", "Cash Application", "Treasury / AR Manager",
                 f"Unapplied cash is {round(pct, 1)}% of receipts (limit {P.HIGH_UNAPPLIED_CASH_WARNING_PCT}%).",
                 warn, u["unapplied_count"], u["unapplied_cash_usd"], ["cash_application", "bank_receipts"],
                 "Clear unapplied cash to recognize true collections.")


def ctl_soft_high_dispute_rate(dfs, period):
    summ = core.build_executive_o2c_summary(dfs, period)
    warn = summ["disputed_ar_pct"] > P.HIGH_DISPUTE_RATE_WARNING_PCT
    return _soft("S6_HIGH_DISPUTE_RATE", "Dispute rate", "Credit & Disputes",
                 "Credit & Commercial Finance Manager",
                 f"Disputed AR is {summ['disputed_ar_pct']}% of open AR (limit {P.HIGH_DISPUTE_RATE_WARNING_PCT}%).",
                 warn, 0, summ["disputed_ar_usd"], ["disputes", "invoices"],
                 "Drive root-cause fixes on the top dispute reasons.")


def ctl_soft_manual_credit_memo(dfs, period):
    n_cm = len(dfs["credit_memos"])
    n_inv = len(dfs["invoices"]) or 1
    pct = n_cm / n_inv * 100.0
    warn = pct > P.MANUAL_CREDIT_MEMO_WARNING_PCT
    amt = round(float(dfs["credit_memos"]["credit_amount_usd"].sum()), 2)
    return _soft("S7_MANUAL_CREDIT_MEMO_RATE", "Manual credit memo rate", "Billing Operations",
                 "Billing Manager",
                 f"Credit memos are {round(pct, 1)}% of invoices (limit {P.MANUAL_CREDIT_MEMO_WARNING_PCT}%).",
                 warn, n_cm, amt, ["credit_memos", "invoices"],
                 "Investigate credit-memo drivers (pricing, billing errors).")


def ctl_soft_non_standard_terms(dfs, period):
    cust = dfs["customers"]
    bad = cust[~cust["payment_terms"].isin(P.STANDARD_PAYMENT_TERMS)]
    warn = len(bad) > 0
    return _soft("S8_NON_STANDARD_TERMS", "Non-standard payment terms", "Revenue Operations",
                 "Revenue Operations Manager",
                 f"{len(bad)} customers are on non-standard payment terms.",
                 warn, len(bad), 0.0, ["customers"],
                 "Review non-standard terms for cash-flow and policy alignment.",
                 _ids(bad["customer_id"]))


def ctl_soft_stale_credit_review(dfs, period):
    asof = core.as_of_date(period)
    cust = dfs["customers"].copy()
    cust["days_since_review"] = (asof - cust["last_review_date"]).dt.days
    bad = cust[cust["days_since_review"] > P.STALE_CREDIT_REVIEW_DAYS]
    warn = len(bad) > 0
    return _soft("S9_STALE_CREDIT_REVIEW", "Stale customer credit review", "Credit & Commercial Finance",
                 "Credit & Commercial Finance Manager",
                 f"{len(bad)} customers have a credit review older than {P.STALE_CREDIT_REVIEW_DAYS} days.",
                 warn, len(bad), 0.0, ["customers", "credit_limits"],
                 "Schedule overdue credit reviews.", _ids(bad["customer_id"]))


def ctl_soft_fx_gain_loss(dfs, period):
    ca = dfs["cash_application"]
    fx_abs = float(ca["fx_gain_loss"].abs().sum())
    warn = fx_abs > P.FX_GAIN_LOSS_WARNING_USD
    return _soft("S10_FX_GAIN_LOSS", "FX gain/loss on cash application", "Cash Application",
                 "Treasury / AR Manager",
                 f"Absolute FX gain/loss on applications is {round(fx_abs, 2)} (limit {P.FX_GAIN_LOSS_WARNING_USD}).",
                 warn, 0, fx_abs, ["cash_application"],
                 "Review FX policy and revaluation on multi-currency receipts.")


HARD_CONTROLS = [
    ctl_crm_closed_won_to_contract, ctl_contract_to_order, ctl_order_to_billing_schedule,
    ctl_billing_completeness, ctl_invoice_accuracy, ctl_po_required, ctl_invoice_duplicate,
    ctl_ar_subledger_completeness, ctl_cash_receipt_to_bank, ctl_cash_application_completeness,
    ctl_revenue_recognition_cutoff, ctl_deferred_revenue_rollforward, ctl_credit_limit_breach,
    ctl_credit_hold_new_order, ctl_dispute_collection_block,
]
SOFT_CONTROLS = [
    ctl_soft_billing_timeliness, ctl_soft_high_dso, ctl_soft_aging_concentration,
    ctl_soft_broken_promise, ctl_soft_high_unapplied_cash, ctl_soft_high_dispute_rate,
    ctl_soft_manual_credit_memo, ctl_soft_non_standard_terms, ctl_soft_stale_credit_review,
    ctl_soft_fx_gain_loss,
]


def run_all_controls(dfs, period=P.DEFAULT_PERIOD):
    """Run every hard and soft control; return a list of ControlResult."""
    return [c(dfs, period) for c in HARD_CONTROLS] + [c(dfs, period) for c in SOFT_CONTROLS]


def controls_summary(results):
    hard = [r for r in results if r.severity == "HARD"]
    soft = [r for r in results if r.severity == "SOFT"]
    hard_fail = [r for r in hard if r.status == "FAIL"]
    soft_warn = [r for r in soft if r.status == "WARNING"]
    passed = [r for r in results if r.status == "PASS"]
    return {"total": len(results), "hard": len(hard), "soft": len(soft),
            "hard_failures": len(hard_fail), "soft_warnings": len(soft_warn),
            "pass_count": len(passed),
            "control_pass_rate_pct": round(len(passed) / len(results) * 100.0, 1) if results else 100.0,
            "blocks_reporting": len(hard_fail) > 0,
            "hard_failure_ids": [r.control_id for r in hard_fail]}


def results_to_dataframe(results):
    return pd.DataFrame([r.to_dict() for r in results])


if __name__ == "__main__":
    dfs = core.load()
    results = run_all_controls(dfs)
    df = results_to_dataframe(results)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(df[["control_id", "severity", "status", "failing_record_count",
                  "failing_amount_usd", "blocks_reporting"]].to_string(index=False))
    print()
    print(controls_summary(results))
