"""
connector.py - The swappable source interface.

SourceConnector is the boundary the rest of the system codes against. Today there
are two implementations: SyntheticConnector (the existing Lumen CSVs, untouched)
and QuickBooksConnector (live sandbox -> canonical). NetSuite / SAP / Odoo / Zoho
would each be one more class implementing the same interface; nothing downstream
changes. Every connector emits the SAME canonical tables (schema.CONTRACT_TABLES),
so finance_core and the MCP surface never learn a vendor's object names.
"""

import datetime
import os
from abc import ABC, abstractmethod

from schema import CONTRACT_TABLES, EXTRA_TABLES
import csvio

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTHETIC_DATA_DIR = os.path.join(HERE, "..", "..", "finance-mcp", "data")
_PERIOD_SCOPED = ("pnl_activity", "balance_sheet", "budget")


def period_bounds(period):
    """(start_date, end_date) ISO strings for a YYYY-MM period."""
    y, m = (int(x) for x in period.split("-"))
    start = datetime.date(y, m, 1)
    end = datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


class SourceConnector(ABC):
    """Abstract data source. Subclasses implement canonical_tables()."""

    name = "abstract"

    @abstractmethod
    def canonical_tables(self, period=None):
        """Return the full canonical table set as {table_name: list[dict]}."""

    # ----- source-agnostic fetchers (identical on every connector) --------
    def fetch_chart_of_accounts(self, period=None):
        return self.canonical_tables(period)["chart_of_accounts"]

    def fetch_pnl(self, period):
        return self.canonical_tables(period)["pnl_activity"]

    def fetch_balance_sheet(self, period):
        return self.canonical_tables(period)["balance_sheet"]

    def fetch_trial_balance(self, period):
        return self.canonical_tables(period).get("trial_balance", [])

    def fetch_ar(self, period=None):
        return self.canonical_tables(period)["ar_invoices"]

    def fetch_ap(self, period=None):
        return self.canonical_tables(period)["ap_invoices"]

    def fetch_payments(self, period=None):
        return self.canonical_tables(period).get("payments", [])


class SyntheticConnector(SourceConnector):
    """The existing Lumen synthetic CSVs, read as canonical (they already are)."""

    name = "synthetic"

    def __init__(self, data_dir=SYNTHETIC_DATA_DIR):
        self.data_dir = data_dir

    def canonical_tables(self, period=None):
        tables = {}
        for name in list(CONTRACT_TABLES) + list(EXTRA_TABLES):
            rows = csvio.read_table(os.path.join(self.data_dir, name + ".csv"))
            if period and name in _PERIOD_SCOPED:
                rows = [r for r in rows if r.get("period") == period]
            tables[name] = rows
        return tables


class QuickBooksConnector(SourceConnector):
    """Live QuickBooks Online sandbox -> canonical, via the read-only adapter."""

    name = "quickbooks"

    def __init__(self, adapter, entity_id=None, entity_name=None):
        self.adapter = adapter
        self.entity_id = entity_id or os.environ.get("QBO_ENTITY_ID", "US")
        self.entity_name = entity_name or os.environ.get("QBO_ENTITY_NAME", "QuickBooks Sandbox Co.")
        self._raw_cache = {}

    def extract_raw(self, period):
        """Pull every read-only response needed for `period` (cached per period)."""
        if period in self._raw_cache:
            return self._raw_cache[period]
        start, end = period_bounds(period)
        a = self.adapter
        raw = {
            "profit_and_loss": a.profit_and_loss(start, end),
            "balance_sheet": a.balance_sheet(end),
            "trial_balance": a.trial_balance(start, end),
            "aged_receivables": a.aged_receivables(end),
            "aged_payables": a.aged_payables(end),
            "accounts": a.accounts(),
            "invoices": a.invoices(),
            "bills": a.bills(),
            "payments": a.payments(),
            "bill_payments": a.bill_payments(),
            "customers": a.customers(),
            "vendors": a.vendors(),
            "journal_entries": a.journal_entries(),
        }
        self._raw_cache[period] = raw
        return raw

    def canonical_tables(self, period=None):
        if not period:
            raise ValueError("QuickBooksConnector requires an explicit period (YYYY-MM)")
        import mapper
        return mapper.build_canonical(self.extract_raw(period), self.entity_id, self.entity_name, period)
