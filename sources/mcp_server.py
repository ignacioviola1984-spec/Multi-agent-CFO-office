"""
mcp_server.py - Source-agnostic, read-only MCP surface over the canonical layer.

Same idea as finance-mcp, but the tools read the CANONICAL tables of whatever
source is active (SOURCE=synthetic | quickbooks), never a vendor's objects. Swap
QuickBooks for NetSuite/SAP/Odoo/Zoho and this surface does not change.

Read-only by construction: every tool only reads canonical tables and formats
them; there is no write tool. Run:  python sources/mcp_server.py
"""

import datetime
import os
import sys
from collections import defaultdict

# make the source packages importable (flat-import repo style)
_SRC = os.path.dirname(os.path.abspath(__file__))
for _s in ("canonical", "quickbooks", "snapshots"):
    _p = os.path.join(_SRC, _s)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcp.server.fastmcp import FastMCP
from schema import (PNL_REVENUE, PNL_COGS, PNL_EXPENSE_CODES, BS_ASSET_CODES,
                    BS_LIAB_CODES, BS_EQUITY_CODES, COA_NAME)
import materialize

mcp = FastMCP("finance-canonical")
DEFAULT_PERIOD = os.environ.get("FINANCE_LATEST_PERIOD", "2026-05")
_CONN = {}


def connector():
    """Lazily build the active source connector (so import needs no credentials)."""
    if "c" not in _CONN:
        _CONN["c"] = materialize.build_connector()
    return _CONN["c"]


def _agg(rows):
    a = defaultdict(float)
    for r in rows:
        a[r["account_code"]] += float(r["amount_local"])
    return a


def _money(x):
    return f"USD {x:,.0f}"


def _period_end(period):
    y, m = (int(x) for x in period.split("-"))
    return datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)


@mcp.tool()
def active_source() -> str:
    """Name of the active data source (synthetic or quickbooks) behind these tools."""
    return f"Active source: {connector().name}"


@mcp.tool()
def get_pnl(period: str = DEFAULT_PERIOD) -> str:
    """Consolidated P&L for a period (YYYY-MM), in USD, from the active source."""
    a = _agg(connector().fetch_pnl(period))
    rev, cogs = a[PNL_REVENUE], a[PNL_COGS]
    gross = rev - cogs
    opex = sum(a[c] for c in PNL_EXPENSE_CODES)
    oi = gross - opex
    gm = (gross / rev * 100) if rev else 0
    om = (oi / rev * 100) if rev else 0
    return "\n".join([
        f"P&L | period {period} | source {connector().name} | USD",
        f"  Revenue            {_money(rev)}",
        f"  Cost of revenue    {_money(-cogs)}",
        f"  Gross profit       {_money(gross)}   ({gm:.1f}%)",
        f"  Operating expenses {_money(-opex)}",
        f"  Operating income   {_money(oi)}   ({om:.1f}%)",
    ])


@mcp.tool()
def get_balance_sheet(period: str = DEFAULT_PERIOD) -> str:
    """Consolidated balance sheet for a period (YYYY-MM), in USD."""
    a = _agg(connector().fetch_balance_sheet(period))
    assets = sum(a[c] for c in BS_ASSET_CODES)
    liab = sum(a[c] for c in BS_LIAB_CODES)
    equity = sum(a[c] for c in BS_EQUITY_CODES)
    return "\n".join([
        f"Balance sheet | period {period} | source {connector().name} | USD",
        f"  Total assets       {_money(assets)}",
        f"  Total liabilities  {_money(liab)}",
        f"  Total equity       {_money(equity)}",
        f"  Check (A - L - E)  {_money(assets - liab - equity)}",
    ])


@mcp.tool()
def get_trial_balance(period: str = DEFAULT_PERIOD) -> str:
    """Trial balance (per canonical account) for a period, with debit/credit totals."""
    rows = connector().fetch_trial_balance(period)
    lines = [f"Trial balance | period {period} | source {connector().name} | USD"]
    debit = credit = 0.0
    for r in sorted(rows, key=lambda x: x["account_code"]):
        d, c = float(r["debit"]), float(r["credit"])
        debit += d
        credit += c
        lines.append(f"  {r['account_code']} {COA_NAME.get(r['account_code'], ''):24} "
                     f"D {_money(d):>16}  C {_money(c):>16}")
    lines.append(f"  {'TOTALS':28} D {_money(debit):>16}  C {_money(credit):>16}")
    return "\n".join(lines)


def _aging(rows, as_of):
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in rows:
        if r["status"] != "open":
            continue
        overdue = (as_of - datetime.date.fromisoformat(r["due_date"])).days
        amt = float(r["amount_local"])
        key = ("current" if overdue <= 0 else "1-30" if overdue <= 30 else
               "31-60" if overdue <= 60 else "61-90" if overdue <= 90 else "90+")
        buckets[key] += amt
    return buckets


@mcp.tool()
def get_ar_aging(period: str = DEFAULT_PERIOD, as_of_date: str = "") -> str:
    """AR aging of open invoices by bucket, as of the period close (or as_of_date)."""
    as_of = datetime.date.fromisoformat(as_of_date) if as_of_date else _period_end(period)
    b = _aging(connector().fetch_ar(period), as_of)
    total = sum(b.values())
    lines = [f"AR aging | as of {as_of} | source {connector().name} | USD"]
    for k, v in b.items():
        lines.append(f"  {k:8} {_money(v)}")
    lines.append(f"  {'total':8} {_money(total)}")
    return "\n".join(lines)


@mcp.tool()
def get_ap_aging(period: str = DEFAULT_PERIOD, as_of_date: str = "") -> str:
    """AP aging of open bills by bucket, as of the period close (or as_of_date)."""
    as_of = datetime.date.fromisoformat(as_of_date) if as_of_date else _period_end(period)
    b = _aging(connector().fetch_ap(period), as_of)
    total = sum(b.values())
    lines = [f"AP aging | as of {as_of} | source {connector().name} | USD"]
    for k, v in b.items():
        lines.append(f"  {k:8} {_money(v)}")
    lines.append(f"  {'total':8} {_money(total)}")
    return "\n".join(lines)


@mcp.tool()
def get_chart_of_accounts() -> str:
    """The canonical roll-up chart of accounts (source-independent)."""
    rows = connector().fetch_chart_of_accounts()
    lines = [f"Chart of accounts | source {connector().name}"]
    for a in rows:
        lines.append(f"  {a['account_code']}  {a['account_name']:24} {a['type']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Order-to-Cash tools (source-agnostic; empty unless the source fills the
# optional canonical tables, e.g. ERPNext). Swapping vendor does not change them.
# --------------------------------------------------------------------------
def _o2c_block(title, rows, amount_key="amount_local", party_key="customer", extra=()):
    lines = [f"{title} | source {connector().name} | {len(rows)} record(s)"]
    total = 0.0
    for r in rows:
        amt = float(r.get(amount_key, 0) or 0)
        total += amt
        tail = "  ".join(f"{k}={r.get(k, '')}" for k in extra)
        lines.append(f"  {r.get(party_key, ''):24} {r.get('currency', ''):4} {amt:>14,.0f}  {tail}".rstrip())
    lines.append(f"  {'TOTAL (document ccy, unconsolidated)':36} {total:>14,.0f}")
    return "\n".join(lines)


@mcp.tool()
def get_sales_orders(period: str = DEFAULT_PERIOD) -> str:
    """Open/!open sales orders for a period (Order-to-Cash). Empty if the source has none."""
    return _o2c_block("Sales orders", connector().fetch_sales_orders(period),
                      extra=("order_date", "delivery_date", "status"))


@mcp.tool()
def get_quotations(period: str = DEFAULT_PERIOD) -> str:
    """Quotations for a period (Order-to-Cash). Empty if the source has none."""
    return _o2c_block("Quotations", connector().fetch_quotations(period),
                      extra=("quotation_date", "valid_till", "status"))


@mcp.tool()
def get_credit_notes(period: str = DEFAULT_PERIOD) -> str:
    """Credit notes / sales returns for a period. Empty if the source has none."""
    return _o2c_block("Credit notes", connector().fetch_credit_notes(period),
                      extra=("issue_date", "against_invoice", "status"))


@mcp.tool()
def get_collections(period: str = DEFAULT_PERIOD) -> str:
    """Collections reminders (dunning / payment requests). Empty if the source has none."""
    return _o2c_block("Collections reminders", connector().fetch_collections(period),
                      extra=("reminder_type", "reminder_date", "status"))


@mcp.tool()
def get_cash_bank(period: str = DEFAULT_PERIOD) -> str:
    """Cash / bank accounts and balances. Empty if the source has none."""
    return _o2c_block("Cash & bank", connector().fetch_cash_bank(period),
                      amount_key="balance", party_key="account_name", extra=("bank",))


if __name__ == "__main__":
    mcp.run()
