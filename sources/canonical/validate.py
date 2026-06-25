"""
validate.py - Deterministic validations on canonical tables BEFORE the engine
sees them. Pure code; no model. The result (pass/fail + per-check detail) is
written into the snapshot manifest, and the CLI exits non-zero on failure so it
gates a pipeline like the eval harness does.

Checks:
  - record_counts > 0 on the essential tables
  - balance sheet foots: Assets = Liabilities + Equity
  - trial balance balances: total debits = total credits
  - AR subledger ties to the AR control account (open invoices = BS 1100)
  - P&L / balance internally consistent (no negative roll-up magnitudes, assets > 0)
  - no postings dated after the period close
  - every amount row carries a known currency
"""

import datetime

from schema import (CONTRACT_TABLES, BS_ASSET_CODES, BS_LIAB_CODES, BS_EQUITY_CODES,
                    BS_AR, PNL_REVENUE, PNL_COGS, PNL_EXPENSE_CODES, OPEN, REPORTING_CURRENCY)

TOLERANCE_USD = 1.0
ESSENTIAL_NONEMPTY = ("entities", "chart_of_accounts", "pnl_activity", "balance_sheet")
KNOWN_CURRENCIES = {REPORTING_CURRENCY}


def _period_end(period):
    y, m = (int(x) for x in period.split("-"))
    return (datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1))


def _sum(rows, codes):
    return round(sum(float(r["amount_local"]) for r in rows if r["account_code"] in codes), 2)


def _check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def validate_canonical(tables, period, ar_tie_tolerance=TOLERANCE_USD):
    """Return {pass, checks, record_counts}. Pure and deterministic."""
    checks = []
    counts = {name: len(tables.get(name, [])) for name in CONTRACT_TABLES}
    counts.update({name: len(tables.get(name, [])) for name in tables if name not in counts})

    # 1) essential tables non-empty
    for t in ESSENTIAL_NONEMPTY:
        checks.append(_check(f"records:{t}", counts.get(t, 0) > 0, f"{counts.get(t, 0)} rows"))

    bs = tables.get("balance_sheet", [])
    assets = _sum(bs, BS_ASSET_CODES)
    liab = _sum(bs, BS_LIAB_CODES)
    equity = _sum(bs, BS_EQUITY_CODES)

    # 2) balance sheet foots
    foot = round(assets - (liab + equity), 2)
    checks.append(_check("balance_sheet_foots", abs(foot) <= TOLERANCE_USD,
                         f"A {assets:,.2f} - (L {liab:,.2f} + E {equity:,.2f}) = {foot:,.2f}"))

    # 3) trial balance balances (debits = credits)
    tb = tables.get("trial_balance", [])
    debits = round(sum(float(r["debit"]) for r in tb), 2)
    credits = round(sum(float(r["credit"]) for r in tb), 2)
    checks.append(_check("trial_balance_balances", abs(debits - credits) <= TOLERANCE_USD,
                         f"debits {debits:,.2f} vs credits {credits:,.2f}"))

    # 4) AR subledger ties to the AR control account
    open_ar = round(sum(float(r["amount_local"]) for r in tables.get("ar_invoices", [])
                        if r["status"] == OPEN), 2)
    control_ar = _sum(bs, (BS_AR,))
    tol = max(ar_tie_tolerance, abs(control_ar) * 0.01)
    checks.append(_check("ar_subledger_ties_to_control", abs(open_ar - control_ar) <= tol,
                         f"open AR {open_ar:,.2f} vs control(1100) {control_ar:,.2f}"))

    # 5) P&L / balance internal consistency
    pnl = tables.get("pnl_activity", [])
    rev = _sum(pnl, (PNL_REVENUE,))
    cogs = _sum(pnl, (PNL_COGS,))
    opex = _sum(pnl, PNL_EXPENSE_CODES)
    no_negative = all(float(r["amount_local"]) >= 0 for r in pnl)
    checks.append(_check("pnl_balance_consistent", no_negative and assets > 0,
                         f"rev {rev:,.0f}, cogs {cogs:,.0f}, opex {opex:,.0f}, assets {assets:,.0f}"))

    # 6) no postings dated after the period close
    end = _period_end(period)
    future = _future_dated(tables, end)
    checks.append(_check("no_future_dated_postings", not future,
                         f"{len(future)} future-dated: {future[:5]}" if future else "none"))

    # 7) every amount row carries a known currency
    bad_ccy = _bad_currencies(tables)
    checks.append(_check("currency_present_and_known", not bad_ccy,
                         f"{len(bad_ccy)} bad: {bad_ccy[:5]}" if bad_ccy else "all USD"))

    return {"pass": all(c["ok"] for c in checks), "checks": checks, "record_counts": counts}


def _future_dated(tables, end):
    out = []
    date_fields = {"ar_invoices": ("invoice_id", "issue_date"), "ap_invoices": ("bill_id", "issue_date"),
                   "payments": ("payment_id", "txn_date"), "journal_entries": ("je_id", "txn_date")}
    for table, (idf, datef) in date_fields.items():
        for r in tables.get(table, []):
            ds = r.get(datef, "")
            if not ds:
                continue
            try:
                if datetime.date.fromisoformat(ds) > end:
                    out.append(r.get(idf, "?"))
            except ValueError:
                out.append(r.get(idf, "?") + "(bad-date)")
    return out


def _bad_currencies(tables):
    out = []
    for table, cols in CONTRACT_TABLES.items():
        if "currency" not in cols:
            continue
        for r in tables.get(table, []):
            if (r.get("currency") or "") not in KNOWN_CURRENCIES:
                out.append(f"{table}:{r.get('currency')!r}")
    return out
