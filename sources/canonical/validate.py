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

from schema import (CONTRACT_TABLES, EXTRA_TABLES, BS_ASSET_CODES, BS_LIAB_CODES,
                    BS_EQUITY_CODES, BS_AR, PNL_REVENUE, PNL_COGS, PNL_EXPENSE_CODES,
                    OPEN, REPORTING_CURRENCY)

TOLERANCE_USD = 1.0
ESSENTIAL_NONEMPTY = ("entities", "chart_of_accounts", "pnl_activity", "balance_sheet")


def _period_end(period):
    y, m = (int(x) for x in period.split("-"))
    return (datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1))


def _sum(rows, codes):
    return round(sum(float(r["amount_local"]) for r in rows if r["account_code"] in codes), 2)


def _check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _entities_in(rows):
    return sorted({(r.get("entity_id") or "") for r in rows})


def validate_canonical(tables, period, ar_tie_tolerance=TOLERANCE_USD):
    """Return {pass, checks, record_counts}. Pure and deterministic.

    Entity-aware and multi-currency-aware: the foot / trial-balance / AR-tie
    checks run PER entity (a single-entity source like QuickBooks is just one
    group, so behaviour is unchanged), and the known-currency set is derived
    from the source's own fx_rates table (so a multi-currency source passes
    while an unknown currency still fails). This lets the SAME validator gate
    QuickBooks (single-entity/USD) and ERPNext (multi-company/multi-currency)
    without forking."""
    checks = []
    counts = {name: len(tables.get(name, [])) for name in CONTRACT_TABLES}
    counts.update({name: len(tables.get(name, [])) for name in tables if name not in counts})

    # 1) essential tables non-empty
    for t in ESSENTIAL_NONEMPTY:
        checks.append(_check(f"records:{t}", counts.get(t, 0) > 0, f"{counts.get(t, 0)} rows"))

    bs = tables.get("balance_sheet", [])
    entities = _entities_in(bs)

    # 2) balance sheet foots PER entity (Assets = Liabilities + Equity)
    foot_fails = []
    for eid in entities:
        e = [r for r in bs if (r.get("entity_id") or "") == eid]
        a, l, q = _sum(e, BS_ASSET_CODES), _sum(e, BS_LIAB_CODES), _sum(e, BS_EQUITY_CODES)
        if abs(round(a - (l + q), 2)) > TOLERANCE_USD:
            foot_fails.append(f"{eid}:{round(a - (l + q), 2):,.2f}")
    checks.append(_check("balance_sheet_foots", not foot_fails,
                         f"{len(entities)} entity(ies) foot" if not foot_fails else f"off {foot_fails[:5]}"))

    # 3) trial balance balances (debits = credits) PER entity
    tb = tables.get("trial_balance", [])
    tb_fails = []
    for eid in _entities_in(tb):
        e = [r for r in tb if (r.get("entity_id") or "") == eid]
        d = round(sum(float(r["debit"]) for r in e), 2)
        c = round(sum(float(r["credit"]) for r in e), 2)
        if abs(d - c) > TOLERANCE_USD:
            tb_fails.append(f"{eid}:{round(d - c, 2):,.2f}")
    checks.append(_check("trial_balance_balances", not tb_fails,
                         "balanced" if not tb_fails else f"off {tb_fails[:5]}"))

    # 4) AR subledger ties to the AR control account PER entity
    ar = tables.get("ar_invoices", [])
    ar_fails = []
    for eid in entities:
        open_ar = round(sum(float(r["amount_local"]) for r in ar
                            if (r.get("entity_id") or "") == eid and r["status"] == OPEN), 2)
        control = _sum([r for r in bs if (r.get("entity_id") or "") == eid], (BS_AR,))
        tol = max(ar_tie_tolerance, abs(control) * 0.01)
        if abs(open_ar - control) > tol:
            ar_fails.append(f"{eid}: open {open_ar:,.2f} vs control {control:,.2f}")
    checks.append(_check("ar_subledger_ties_to_control", not ar_fails,
                         "ties per entity" if not ar_fails else str(ar_fails[:5])))

    # 5) P&L / balance internal consistency (positive roll-up magnitudes, assets > 0)
    pnl = tables.get("pnl_activity", [])
    rev, cogs, opex = _sum(pnl, (PNL_REVENUE,)), _sum(pnl, (PNL_COGS,)), _sum(pnl, PNL_EXPENSE_CODES)
    total_assets = _sum(bs, BS_ASSET_CODES)
    no_negative = all(float(r["amount_local"]) >= 0 for r in pnl)
    checks.append(_check("pnl_balance_consistent", no_negative and total_assets > 0,
                         f"rev {rev:,.0f}, cogs {cogs:,.0f}, opex {opex:,.0f}, assets {total_assets:,.0f}"))

    # 6) no postings dated after the period close
    end = _period_end(period)
    future = _future_dated(tables, end)
    checks.append(_check("no_future_dated_postings", not future,
                         f"{len(future)} future-dated: {future[:5]}" if future else "none"))

    # 7) every amount row carries a currency known to this source (USD + the
    #    currencies the source's fx_rates declare).
    known = {REPORTING_CURRENCY} | {(r.get("currency") or "").upper()
                                    for r in tables.get("fx_rates", []) if r.get("currency")}
    bad_ccy = _bad_currencies(tables, known)
    checks.append(_check("currency_present_and_known", not bad_ccy,
                         f"{len(bad_ccy)} bad: {bad_ccy[:5]}" if bad_ccy else f"all in {sorted(known)}"))

    # 8) fx_rates cover every currency used (so consolidation to USD is possible).
    used = {(e.get("currency") or "").upper() for e in tables.get("entities", []) if e.get("currency")}
    used |= {c for c in (_row_currencies(tables)) if c}
    fx_ccy = {(r.get("currency") or "").upper() for r in tables.get("fx_rates", [])
              if r.get("period") == period and r.get("currency")}
    missing_fx = sorted(used - fx_ccy)
    checks.append(_check("fx_rates_cover_currencies", not missing_fx,
                         f"{len(used)} currency(ies) covered" if not missing_fx
                         else f"missing fx for {missing_fx[:5]}"))

    return {"pass": all(c["ok"] for c in checks), "checks": checks, "record_counts": counts}


def _row_currencies(tables):
    out = set()
    for name, cols in {**CONTRACT_TABLES, **EXTRA_TABLES}.items():
        if "currency" not in cols:
            continue
        for r in tables.get(name, []):
            c = (r.get("currency") or "").upper()
            if c:
                out.add(c)
    return out


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


def _bad_currencies(tables, known):
    out = []
    for table, cols in {**CONTRACT_TABLES, **EXTRA_TABLES}.items():
        if "currency" not in cols:
            continue
        for r in tables.get(table, []):
            if (r.get("currency") or "").upper() not in known:
                out.append(f"{table}:{r.get('currency')!r}")
    return out
