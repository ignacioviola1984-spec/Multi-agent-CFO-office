"""
o2c_core.py - Deterministic Order-to-Cash calculation engine.

EVERY business number in the control tower is computed here, in code, from the
source records. Agents narrate and prioritize these numbers; they never invent
them. Functions return DataFrames or dicts with explicit columns so the output
is traceable to source rows and reproducible run to run.

All consolidated amounts are in USD (converted with the period FX table in
o2c_data_loader). Period is YYYY-MM; the as-of date is the last day of the month.
"""

import pandas as pd

try:
    import o2c_policy as P
    import o2c_data_loader as loader
except ImportError:                                    # pragma: no cover
    from . import o2c_policy as P
    from . import o2c_data_loader as loader


# --------------------------------------------------------------------------
# Period helpers
# --------------------------------------------------------------------------
def period_bounds(period):
    """Return (start_timestamp, end_timestamp) for a YYYY-MM period."""
    y, m = (int(x) for x in period.split("-"))
    start = pd.Timestamp(y, m, 1)
    end = start + pd.offsets.MonthEnd(0)
    return start, end


def as_of_date(period):
    return period_bounds(period)[1]


def _aging_bucket(days):
    if days <= 0:
        return "current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 120:
        return "91-120"
    return "120+"


def load(data_dir=loader.DATA_DIR):
    """Convenience: load + validate + normalize the datasets."""
    return loader.load_o2c_data(data_dir)


# --------------------------------------------------------------------------
# Cash application helpers (cash_application has no currency column, so we take
# it from the linked invoice)
# --------------------------------------------------------------------------
def _applications_usd(dfs):
    """cash_application rows with applied_amount_usd derived from invoice ccy."""
    ca = dfs["cash_application"].copy()
    inv_ccy = dfs["invoices"][["invoice_id", "currency"]].rename(columns={"currency": "_inv_ccy"})
    ca = ca.merge(inv_ccy, on="invoice_id", how="left")
    ca["_inv_ccy"] = ca["_inv_ccy"].fillna("USD")
    ca["applied_amount_usd"] = loader.to_usd(ca["applied_amount"], ca["_inv_ccy"])
    return ca


# --------------------------------------------------------------------------
# Open AR / aging
# --------------------------------------------------------------------------
def calculate_invoice_open_amounts(dfs, period=P.DEFAULT_PERIOD):
    """Per-invoice open balance = total invoiced - applied cash - approved credits.

    Returns one row per invoice with USD amounts, days overdue, aging bucket, and
    a disputed (cash-blocked) flag. This is the spine of AR.
    """
    asof = as_of_date(period)
    inv = dfs["invoices"].copy()

    apps = _applications_usd(dfs)
    applied = (apps[apps["application_status"] == "applied"]
               .groupby("invoice_id")["applied_amount_usd"].sum().rename("applied_usd"))

    cm = dfs["credit_memos"]
    credited = (cm[cm["approval_status"] == "approved"]
                .groupby("invoice_id")["credit_amount_usd"].sum().rename("credited_usd"))

    disp = dfs["disputes"]
    blocked_ids = set(disp[disp["cash_blocked_flag"] == 1]["invoice_id"])

    out = inv[["invoice_id", "customer_id", "legal_entity", "currency", "due_date",
               "invoice_date", "total_invoice_amount_usd", "gl_ar_account",
               "invoice_status"]].copy()
    out = out.merge(applied, on="invoice_id", how="left").merge(credited, on="invoice_id", how="left")
    out["applied_usd"] = out["applied_usd"].fillna(0.0)
    out["credited_usd"] = out["credited_usd"].fillna(0.0)
    out["open_usd"] = (out["total_invoice_amount_usd"] - out["applied_usd"]
                       - out["credited_usd"]).round(2)
    out["days_overdue"] = (asof - out["due_date"]).dt.days
    out["aging_bucket"] = out["days_overdue"].apply(_aging_bucket)
    out["is_disputed"] = out["invoice_id"].isin(blocked_ids)
    out["is_open"] = out["open_usd"] > 1.0
    return out


def calculate_ar_open_items(dfs, period=P.DEFAULT_PERIOD):
    """Only invoices with a real open balance (the working AR subledger)."""
    items = calculate_invoice_open_amounts(dfs, period)
    return items[items["is_open"]].reset_index(drop=True)


def calculate_ar_aging(dfs, period=P.DEFAULT_PERIOD):
    """Open AR by aging bucket (USD) with counts; plus current/overdue split."""
    items = calculate_ar_open_items(dfs, period)
    order = ["current", "1-30", "31-60", "61-90", "91-120", "120+"]
    g = (items.groupby("aging_bucket")["open_usd"].agg(["sum", "count"])
         .reindex(order).fillna(0.0))
    rows = [{"aging_bucket": b, "open_ar_usd": round(float(g.loc[b, "sum"]), 2),
             "invoice_count": int(g.loc[b, "count"])} for b in order]
    aging = pd.DataFrame(rows)
    total = round(float(items["open_usd"].sum()), 2)
    current = round(float(items[items["days_overdue"] <= 0]["open_usd"].sum()), 2)
    return {"by_bucket": aging, "total_open_ar_usd": total, "current_ar_usd": current,
            "overdue_ar_usd": round(total - current, 2),
            "ar_90_plus_usd": round(float(aging[aging["aging_bucket"].isin(["91-120", "120+"])]
                                          ["open_ar_usd"].sum()), 2)}


# --------------------------------------------------------------------------
# Opportunity-to-cash chain
# --------------------------------------------------------------------------
def build_opportunity_to_cash_chain(dfs, period=P.DEFAULT_PERIOD):
    """Trace conversion at each handoff and flag breaks.

    Returns a dict of frames, each carrying a has_* flag the controls consume:
    closed-won -> contract -> order -> billing schedule -> invoice.
    """
    asof = as_of_date(period)
    opp, ctr, orders, bill = dfs["opportunities"], dfs["contracts"], dfs["orders"], dfs["billing"]

    won = opp[opp["closed_won_flag"] == 1].copy()
    won["has_contract"] = won["opportunity_id"].isin(set(ctr["opportunity_id"]))

    active_ctr = ctr[ctr["contract_status"] == "active"].copy()
    active_ctr["has_order"] = active_ctr["contract_id"].isin(set(orders["contract_id"]))

    active_ord = orders[orders["order_status"] == "active"].copy()
    active_ord["has_billing"] = active_ord["order_id"].isin(set(bill["order_id"]))

    due = bill[(bill["scheduled_invoice_date"] <= asof)].copy()
    due["billable"] = due["billing_status"].isin(["billable", "billed"])
    due["has_invoice"] = due["invoice_id"].astype(str).str.len() > 0
    due["is_blocked"] = due["billing_status"] == "blocked"

    return {"closed_won": won, "active_contracts": active_ctr,
            "active_orders": active_ord, "due_billing": due}


# --------------------------------------------------------------------------
# Billing completeness / timeliness / accuracy / unbilled revenue
# --------------------------------------------------------------------------
def calculate_unbilled_revenue(dfs, period=P.DEFAULT_PERIOD):
    """Scheduled, billable, due bill lines with no invoice and no valid block.

    This is the revenue-leakage candidate: work delivered/scheduled that was
    never billed.
    """
    chain = build_opportunity_to_cash_chain(dfs, period)
    due = chain["due_billing"]
    unbilled = due[(due["billing_status"] == "billable")
                   & (~due["has_invoice"])
                   & (due["billing_exception_reason"].fillna("") == "")].copy()
    unbilled["scheduled_bill_amount_usd"] = loader.to_usd(
        unbilled["scheduled_bill_amount"], unbilled["currency"])
    total = round(float(unbilled["scheduled_bill_amount_usd"].sum()), 2)
    return {"unbilled": unbilled[["billing_schedule_id", "contract_id", "order_id", "customer_id",
                                  "scheduled_invoice_date", "scheduled_bill_amount_usd", "currency"]],
            "unbilled_amount_usd": total, "unbilled_count": int(len(unbilled))}


def calculate_billing_completeness(dfs, period=P.DEFAULT_PERIOD):
    """Invoiced amount vs total scheduled-and-due billable amount."""
    chain = build_opportunity_to_cash_chain(dfs, period)
    due = chain["due_billing"]
    billable = due[due["billing_status"].isin(["billable", "billed"])].copy()
    billable["scheduled_usd"] = loader.to_usd(billable["scheduled_bill_amount"], billable["currency"])
    scheduled_total = float(billable["scheduled_usd"].sum())
    invoiced_total = float(billable[billable["has_invoice"]]["scheduled_usd"].sum())
    pct = (invoiced_total / scheduled_total * 100.0) if scheduled_total else 100.0
    unb = calculate_unbilled_revenue(dfs, period)
    return {"scheduled_due_usd": round(scheduled_total, 2),
            "invoiced_usd": round(invoiced_total, 2),
            "billing_completeness_pct": round(pct, 2),
            "unbilled_amount_usd": unb["unbilled_amount_usd"],
            "unbilled_count": unb["unbilled_count"]}


def calculate_billing_timeliness(dfs, period=P.DEFAULT_PERIOD):
    """Share of issued invoices billed within the allowed delay vs schedule."""
    bill = dfs["billing"]
    inv = dfs["invoices"][["invoice_id", "invoice_date"]]
    billed = bill[bill["invoice_id"].astype(str).str.len() > 0].merge(
        inv, on="invoice_id", how="inner")
    billed = billed.dropna(subset=["invoice_date", "scheduled_invoice_date"])
    billed["delay_days"] = (billed["invoice_date"] - billed["scheduled_invoice_date"]).dt.days
    on_time = billed[billed["delay_days"] <= P.MAX_INVOICE_DELAY_DAYS]
    total = len(billed)
    pct = (len(on_time) / total * 100.0) if total else 100.0
    late = billed[billed["delay_days"] > P.MAX_INVOICE_DELAY_DAYS]
    return {"billed_count": int(total), "on_time_count": int(len(on_time)),
            "billing_timeliness_pct": round(pct, 2), "late_count": int(len(late)),
            "late": late[["invoice_id", "scheduled_invoice_date", "invoice_date", "delay_days"]]}


def calculate_invoice_accuracy(dfs, period=P.DEFAULT_PERIOD):
    """Invoice amount vs the scheduled bill amount it came from, in USD."""
    bill = dfs["billing"]
    billed = bill[bill["invoice_id"].astype(str).str.len() > 0].copy()
    billed["scheduled_usd"] = loader.to_usd(billed["scheduled_bill_amount"], billed["currency"])
    inv = dfs["invoices"][["invoice_id", "invoice_amount_usd"]]   # customer_id comes from billing
    j = billed.merge(inv, on="invoice_id", how="inner")
    j["diff_usd"] = (j["invoice_amount_usd"] - j["scheduled_usd"]).round(2)
    j["tolerance_usd"] = j["scheduled_usd"].apply(P.invoice_tolerance_usd)
    j["is_mismatch"] = j["diff_usd"].abs() > j["tolerance_usd"]
    total = len(j)
    mism = j[j["is_mismatch"]]
    pct = ((total - len(mism)) / total * 100.0) if total else 100.0
    return {"checked_count": int(total), "mismatch_count": int(len(mism)),
            "invoice_accuracy_pct": round(pct, 2),
            "mismatch_amount_usd": round(float(mism["diff_usd"].abs().sum()), 2),
            "mismatches": mism[["invoice_id", "billing_schedule_id", "customer_id",
                                "scheduled_usd", "invoice_amount_usd", "diff_usd"]]}


# --------------------------------------------------------------------------
# Cash application / unapplied cash
# --------------------------------------------------------------------------
def calculate_cash_application_status(dfs, period=P.DEFAULT_PERIOD):
    """Bank cash received vs cash applied to AR; the application rate."""
    rec = dfs["bank_receipts"]
    received_usd = float((rec["receipt_amount"] * rec["currency"].map(P.FX_TO_USD).fillna(1.0)).sum())
    apps = _applications_usd(dfs)
    applied_usd = float(apps[apps["application_status"] == "applied"]["applied_amount_usd"].sum())
    rate = (applied_usd / received_usd * 100.0) if received_usd else 100.0
    matched = rec[rec["matched_status"] == "matched"]
    unmatched = rec[rec["matched_status"] != "matched"]
    return {"received_usd": round(received_usd, 2), "applied_usd": round(applied_usd, 2),
            "cash_application_rate_pct": round(rate, 2),
            "matched_receipt_count": int(len(matched)),
            "unmatched_receipt_count": int(len(unmatched))}


def calculate_unapplied_cash(dfs, period=P.DEFAULT_PERIOD):
    """Cash received but not yet applied: unapplied applications + unmatched receipts."""
    apps = _applications_usd(dfs)
    pmt = dfs["payments"][["payment_id", "payment_amount", "currency"]].copy()
    pmt["payment_amount_usd"] = loader.to_usd(pmt["payment_amount"], pmt["currency"])
    unapp = apps[apps["application_status"] == "unapplied"].merge(
        pmt[["payment_id", "payment_amount_usd"]], on="payment_id", how="left")
    unapplied_app_usd = round(float(unapp["payment_amount_usd"].fillna(0.0).sum()), 2)

    rec = dfs["bank_receipts"]
    rec_usd = rec.copy()
    rec_usd["receipt_amount_usd"] = loader.to_usd(rec_usd["receipt_amount"], rec_usd["currency"])
    unmatched = rec_usd[rec_usd["matched_status"] != "matched"]
    unmatched_usd = round(float(unmatched["receipt_amount_usd"].sum()), 2)
    return {"unapplied_application_usd": unapplied_app_usd,
            "unmatched_receipt_usd": unmatched_usd,
            "unapplied_cash_usd": round(unapplied_app_usd, 2),
            "unapplied_count": int(len(unapp)),
            "unapplied": unapp[["cash_application_id", "payment_id", "invoice_id", "customer_id",
                                "payment_amount_usd", "unapplied_reason"]]}


# --------------------------------------------------------------------------
# Revenue recognition / deferred revenue
# --------------------------------------------------------------------------
def calculate_revenue_recognition_rollforward(dfs, period=P.DEFAULT_PERIOD):
    """Recognized revenue to date and in-period, plus cutoff exceptions."""
    rev = dfs["revenue"].copy()
    recognized = rev[rev["recognition_status"] == "recognized"]
    recognized_to_date = round(float(recognized["recognized_revenue_usd"].sum()), 2)
    in_period = round(float(recognized[recognized["revenue_month"] == period]
                            ["recognized_revenue_usd"].sum()), 2)

    # cutoff: recognized before service start, or after contract end w/o renewal
    inv = dfs["invoices"][["invoice_id", "service_period_start", "service_period_end"]]
    ctr = dfs["contracts"][["contract_id", "contract_end_date", "auto_renew_flag"]]
    chk = recognized.merge(inv, on="invoice_id", how="left").merge(ctr, on="contract_id", how="left")
    chk["rev_ts"] = pd.to_datetime(chk["revenue_month"] + "-01", errors="coerce")
    start_month = chk["service_period_start"].dt.to_period("M").dt.to_timestamp()
    end_month = chk["contract_end_date"].dt.to_period("M").dt.to_timestamp()
    chk["before_service_start"] = chk["rev_ts"] < start_month
    chk["after_contract_end_no_renew"] = (chk["rev_ts"] > end_month) & (chk["auto_renew_flag"] == 0)
    cutoff = chk[chk["before_service_start"] | chk["after_contract_end_no_renew"]]
    return {"recognized_to_date_usd": recognized_to_date,
            "recognized_in_period_usd": in_period,
            "cutoff_exception_count": int(len(cutoff)),
            "cutoff_exceptions": cutoff[["revenue_schedule_id", "invoice_id", "contract_id",
                                         "revenue_month", "recognized_revenue_usd",
                                         "before_service_start", "after_contract_end_no_renew"]]}


def calculate_deferred_revenue_rollforward(dfs, period=P.DEFAULT_PERIOD):
    """Check the rollforward foots: closing == opening + billings - recognized
    + adjustments + fx_impact, per row, within tolerance."""
    d = dfs["deferred"].copy()
    d["expected_closing"] = (d["opening_deferred_revenue"] + d["billings"]
                             - d["recognized_revenue"] + d["adjustments"] + d["fx_impact"]).round(2)
    d["foot_diff"] = (d["closing_deferred_revenue"] - d["expected_closing"]).round(2)
    d["breaks"] = d["foot_diff"].abs() > P.DEFERRED_REVENUE_ROLLFORWARD_TOLERANCE
    breaks = d[d["breaks"]]
    ending = round(float(d[d["period"] == period]["closing_deferred_revenue_usd"].sum()), 2)
    return {"deferred_ending_usd": ending,
            "rollforward_break_count": int(len(breaks)),
            "rollforward_break_amount_usd": round(float(
                loader.to_usd(breaks["foot_diff"], breaks["currency"]).abs().sum()), 2),
            "breaks": breaks[["period", "contract_id", "customer_id", "opening_deferred_revenue",
                              "billings", "recognized_revenue", "closing_deferred_revenue",
                              "expected_closing", "foot_diff"]]}


# --------------------------------------------------------------------------
# Credit exposure
# --------------------------------------------------------------------------
def calculate_credit_exposure(dfs, period=P.DEFAULT_PERIOD):
    """Latest credit policy per customer; flag exposure above the approved limit."""
    cl = dfs["credit_limits"].sort_values("effective_date")
    latest = cl.groupby("customer_id", as_index=False).last()
    cust = dfs["customers"][["customer_id", "customer_name", "customer_status", "risk_tier"]]
    j = latest.merge(cust, on="customer_id", how="left")
    j["over_limit_usd"] = (j["current_exposure_amount_usd"] - j["credit_limit_usd"]).round(2)
    j["is_breach"] = (j["over_limit_usd"] > 0) & (j["hold_flag"] == 0)
    breaches = j[j["is_breach"]]
    on_hold = j[(j["hold_flag"] == 1) | (j["credit_status"] == "hold")]
    return {"total_exposure_usd": round(float(j["current_exposure_amount_usd"].sum()), 2),
            "breach_count": int(len(breaches)),
            "breach_amount_usd": round(float(breaches["over_limit_usd"].sum()), 2),
            "hold_exposure_usd": round(float(on_hold["current_exposure_amount_usd"].sum()), 2),
            "breaches": breaches[["customer_id", "customer_name", "credit_limit_usd",
                                  "current_exposure_amount_usd", "over_limit_usd", "risk_score"]],
            "policies": j}


# --------------------------------------------------------------------------
# Collections forecast (disputed cash excluded)
# --------------------------------------------------------------------------
def calculate_collections_forecast(dfs, period=P.DEFAULT_PERIOD):
    """Expected cash by horizon from open, non-disputed AR plus promises to pay.

    Disputed (cash-blocked) invoices are routed out of the normal forecast.
    Deterministic: expected cash in a horizon = open non-disputed AR with a due
    date on/before the horizon end (overdue collects first), capped at total AR.
    """
    asof = as_of_date(period)
    items = calculate_ar_open_items(dfs, period)
    workable = items[~items["is_disputed"]].copy()

    def due_by(days):
        horizon = asof + pd.Timedelta(days=days)
        return round(float(workable[workable["due_date"] <= horizon]["open_usd"].sum()), 2)

    # broken promises: promise date passed, invoice still open
    col = dfs["collections"]
    open_ids = set(items["invoice_id"])
    promises = col[col["promise_to_pay_date"].notna()].copy()
    promises["promised_amount"] = pd.to_numeric(promises["promised_amount"], errors="coerce").fillna(0.0)
    broken = promises[(promises["promise_to_pay_date"] < asof)
                      & (promises["invoice_id"].isin(open_ids))]
    inv_ccy = dfs["invoices"][["invoice_id", "currency"]]
    broken = broken.merge(inv_ccy, on="invoice_id", how="left")
    broken_usd = round(float(loader.to_usd(broken["promised_amount"],
                                           broken["currency"].fillna("USD")).sum()), 2)
    return {"expected_cash_7d_usd": due_by(7), "expected_cash_30d_usd": due_by(30),
            "expected_cash_13w_usd": due_by(91),
            "workable_ar_usd": round(float(workable["open_usd"].sum()), 2),
            "disputed_excluded_usd": round(float(items[items["is_disputed"]]["open_usd"].sum()), 2),
            "broken_promise_count": int(len(broken)), "broken_promise_amount_usd": broken_usd,
            "broken_promises": broken[["invoice_id", "customer_id", "promise_to_pay_date",
                                       "promised_amount"]]}


# --------------------------------------------------------------------------
# DSO and the bookings->billings->revenue->cash bridge
# --------------------------------------------------------------------------
def calculate_dso(dfs, period=P.DEFAULT_PERIOD):
    """DSO and best-possible DSO on a trailing-3-month billings basis."""
    asof = as_of_date(period)
    win_start = (asof + pd.offsets.MonthEnd(0) - pd.offsets.MonthBegin(3))
    inv = dfs["invoices"]
    trailing = inv[(inv["invoice_date"] >= win_start) & (inv["invoice_date"] <= asof)]
    trailing_billings = float(trailing["invoice_amount_usd"].sum())
    days = (asof - win_start).days + 1
    aging = calculate_ar_aging(dfs, period)
    open_ar = aging["total_open_ar_usd"]
    current_ar = aging["current_ar_usd"]
    dso = (open_ar / trailing_billings * days) if trailing_billings else 0.0
    bpdso = (current_ar / trailing_billings * days) if trailing_billings else 0.0
    return {"dso": round(dso, 1), "best_possible_dso": round(bpdso, 1),
            "trailing_billings_usd": round(trailing_billings, 2), "trailing_days": int(days)}


def build_bookings_billings_revenue_cash_bridge(dfs, period=P.DEFAULT_PERIOD):
    """The headline RevOps bridge for the period, all in USD."""
    start, end = period_bounds(period)
    ctr = dfs["contracts"]
    signed = ctr[(ctr["signed_date"] >= start) & (ctr["signed_date"] <= end)]
    bookings = round(float(signed["contract_value_usd"].sum()), 2)
    contracted_arr = round(float(signed["arr_amount_usd"].sum()), 2)

    inv = dfs["invoices"]
    period_inv = inv[(inv["invoice_date"] >= start) & (inv["invoice_date"] <= end)]
    billings = round(float(period_inv["invoice_amount_usd"].sum()), 2)

    recognized = calculate_revenue_recognition_rollforward(dfs, period)["recognized_in_period_usd"]

    apps = _applications_usd(dfs)
    apps_applied = apps[apps["application_status"] == "applied"].copy()
    apps_applied["applied_date"] = pd.to_datetime(apps_applied["applied_date"], errors="coerce")
    period_cash = apps_applied[(apps_applied["applied_date"] >= start)
                               & (apps_applied["applied_date"] <= end)]
    cash = round(float(period_cash["applied_amount_usd"].sum()), 2)
    return {"period": period, "bookings_usd": bookings, "contracted_arr_usd": contracted_arr,
            "billings_usd": billings, "recognized_revenue_usd": recognized, "cash_collected_usd": cash,
            "bridge": [("Bookings (contracts signed)", bookings),
                       ("Billings (invoiced)", billings),
                       ("Recognized revenue", recognized),
                       ("Cash collected", cash)]}


# --------------------------------------------------------------------------
# Executive summary (numbers only; agents add narrative)
# --------------------------------------------------------------------------
def build_executive_o2c_summary(dfs, period=P.DEFAULT_PERIOD):
    """Assemble the headline O2C numbers for the period (deterministic)."""
    aging = calculate_ar_aging(dfs, period)
    dso = calculate_dso(dfs, period)
    comp = calculate_billing_completeness(dfs, period)
    acc = calculate_invoice_accuracy(dfs, period)
    timeliness = calculate_billing_timeliness(dfs, period)
    cashapp = calculate_cash_application_status(dfs, period)
    unapplied = calculate_unapplied_cash(dfs, period)
    rev = calculate_revenue_recognition_rollforward(dfs, period)
    deferred = calculate_deferred_revenue_rollforward(dfs, period)
    credit = calculate_credit_exposure(dfs, period)
    forecast = calculate_collections_forecast(dfs, period)
    bridge = build_bookings_billings_revenue_cash_bridge(dfs, period)

    items = calculate_ar_open_items(dfs, period)
    disputed_usd = round(float(items[items["is_disputed"]]["open_usd"].sum()), 2)
    disputed_pct = round(disputed_usd / aging["total_open_ar_usd"] * 100.0, 2) \
        if aging["total_open_ar_usd"] else 0.0

    return {
        "period": period,
        "open_ar_usd": aging["total_open_ar_usd"],
        "current_ar_usd": aging["current_ar_usd"],
        "overdue_ar_usd": aging["overdue_ar_usd"],
        "ar_90_plus_usd": aging["ar_90_plus_usd"],
        "dso": dso["dso"], "best_possible_dso": dso["best_possible_dso"],
        "billing_completeness_pct": comp["billing_completeness_pct"],
        "unbilled_revenue_usd": comp["unbilled_amount_usd"],
        "revenue_leakage_usd": comp["unbilled_amount_usd"],
        "billing_timeliness_pct": timeliness["billing_timeliness_pct"],
        "invoice_accuracy_pct": acc["invoice_accuracy_pct"],
        "cash_application_rate_pct": cashapp["cash_application_rate_pct"],
        "unapplied_cash_usd": unapplied["unapplied_cash_usd"],
        "recognized_revenue_usd": rev["recognized_in_period_usd"],
        "deferred_revenue_ending_usd": deferred["deferred_ending_usd"],
        "deferred_rollforward_breaks": deferred["rollforward_break_count"],
        "credit_exposure_usd": credit["total_exposure_usd"],
        "credit_breach_amount_usd": credit["breach_amount_usd"],
        "credit_hold_exposure_usd": credit["hold_exposure_usd"],
        "disputed_ar_usd": disputed_usd, "disputed_ar_pct": disputed_pct,
        "expected_cash_7d_usd": forecast["expected_cash_7d_usd"],
        "expected_cash_30d_usd": forecast["expected_cash_30d_usd"],
        "expected_cash_13w_usd": forecast["expected_cash_13w_usd"],
        "broken_promise_amount_usd": forecast["broken_promise_amount_usd"],
        "bookings_usd": bridge["bookings_usd"], "billings_usd": bridge["billings_usd"],
        "cash_collected_usd": bridge["cash_collected_usd"],
    }


if __name__ == "__main__":
    import json
    dfs = load()
    summary = build_executive_o2c_summary(dfs)
    print(json.dumps(summary, indent=2, default=str))
