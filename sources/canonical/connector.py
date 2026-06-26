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
import importlib.util
import os
import sys
from abc import ABC, abstractmethod

from schema import CONTRACT_TABLES, EXTRA_TABLES
import csvio

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTHETIC_DATA_DIR = os.path.join(HERE, "..", "..", "finance-mcp", "data")
_PERIOD_SCOPED = ("pnl_activity", "balance_sheet", "budget")

# ERPNext vendor modules share file names (adapter.py / mapper.py) with the
# QuickBooks ones, so under the repo's flat-import scheme they would collide.
# Load them by absolute path under UNIQUE module names instead of putting
# sources/erpnext on sys.path. ('auth' is unique to ERPNext, so registering it is
# safe and lets erpnext/adapter.py's `from auth import Config` resolve.)
_ERP_DIR = os.path.abspath(os.path.join(HERE, "..", "erpnext"))
_erp_cache = {}


def load_erpnext():
    """Return (adapter_module, mapper_module) for the ERPNext source, loaded by
    path under unique names so they never shadow the QuickBooks modules."""
    if "adapter" in _erp_cache:
        return _erp_cache["adapter"], _erp_cache["mapper"]

    def _load(modname, filename):
        if modname in sys.modules:
            return sys.modules[modname]
        spec = importlib.util.spec_from_file_location(modname, os.path.join(_ERP_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
        return mod

    _load("auth", "auth.py")                       # for erpnext/adapter.py's `from auth import`
    adapter = _load("erpnext_adapter", "adapter.py")
    mapper = _load("erpnext_mapper", "mapper.py")
    _erp_cache["adapter"], _erp_cache["mapper"] = adapter, mapper
    return adapter, mapper


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

    # ----- Order-to-Cash fetchers (optional; empty unless the source fills them) -
    def fetch_sales_orders(self, period=None):
        return self.canonical_tables(period).get("sales_orders", [])

    def fetch_quotations(self, period=None):
        return self.canonical_tables(period).get("quotations", [])

    def fetch_credit_notes(self, period=None):
        return self.canonical_tables(period).get("credit_notes", [])

    def fetch_collections(self, period=None):
        return self.canonical_tables(period).get("collections_reminders", [])

    def fetch_cash_bank(self, period=None):
        return self.canonical_tables(period).get("cash_bank", [])

    def fetch_opportunities(self, period=None):
        return self.canonical_tables(period).get("crm_opportunities", [])


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


class ERPNextConnector(SourceConnector):
    """Live ERPNext (Frappe) -> canonical, via the read-only adapter. Multi-company
    and multi-currency: each ERPNext Company becomes a canonical entity, so this
    source exercises the consolidation the QuickBooks sandbox could not.

    Vendor-agnostic by construction: every Frappe object name (DocTypes, report
    names, fields, filters) lives in sources/erpnext/ (the adapter's extract_raw
    and the mapper). This class only wires the adapter to the mapper and caches
    per period - it never names a Frappe object."""

    name = "erpnext"

    def __init__(self, adapter, default_country=None):
        self.adapter = adapter
        self.default_country = default_country or os.environ.get("ERPNEXT_DEFAULT_COUNTRY", "United States")
        self._raw_cache = {}

    def extract_raw(self, period):
        """Pull every read-only response needed for `period` (cached). The Frappe
        extraction recipe lives in the adapter; this just caches it."""
        if period not in self._raw_cache:
            self._raw_cache[period] = self.adapter.extract_raw(period)
        return self._raw_cache[period]

    def canonical_tables(self, period=None):
        if not period:
            raise ValueError("ERPNextConnector requires an explicit period (YYYY-MM)")
        _, mapper = load_erpnext()
        return mapper.build_canonical(self.extract_raw(period), period, self.default_country)
