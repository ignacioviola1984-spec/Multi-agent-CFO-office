"""
schema.py - The canonical finance schema. Source-independent by design.

This is the contract the rest of the system speaks. QuickBooks (or NetSuite, SAP,
Odoo, Zoho tomorrow) is mapped INTO this shape; nothing downstream ever sees a
vendor object. Two things live here:

  1. CONTRACT_TABLES - the tables finance_core and the finance-mcp server already
     read from CSV. The canonical layer MUST emit these with byte-identical
     columns so the engine reads a QuickBooks-sourced period exactly as it reads
     the synthetic one, with zero engine changes. The column lists below were
     lifted verbatim from finance-mcp/data/*.csv.

  2. CANONICAL_COA - the rollup chart of accounts the engine computes on (12
     accounts). A source's detailed chart is mapped into these rollup codes; the
     engine never depends on a vendor's account ids or names.

Plus a few richer canonical tables (trial_balance, payments, customers, vendors,
journal_entries) that the engine does not need but the snapshot and the MCP
surface expose for traceability.
"""

# --------------------------------------------------------------------------
# Reporting context for a single-entity / single-currency source (QBO sandbox
# is the US sample company). The synthetic source is multi-entity / multi-ccy;
# these defaults only apply when a connector does not supply its own.
# --------------------------------------------------------------------------
REPORTING_CURRENCY = "USD"

# --------------------------------------------------------------------------
# The engine contract: exact columns of the CSVs finance_core / the MCP server
# load today. Order matters (it is the CSV header we write).
# --------------------------------------------------------------------------
CONTRACT_TABLES = {
    "entities":          ["entity_id", "name", "country", "currency"],
    "fx_rates":          ["period", "currency", "units_per_usd"],
    "chart_of_accounts": ["account_code", "account_name", "type"],
    "pnl_activity":      ["entity_id", "period", "account_code", "amount_local"],
    "balance_sheet":     ["entity_id", "period", "account_code", "amount_local"],
    "budget":            ["entity_id", "period", "account_code", "amount_usd"],
    "ar_invoices":       ["invoice_id", "entity_id", "customer", "currency",
                          "amount_local", "issue_date", "due_date", "status"],
    "ap_invoices":       ["bill_id", "entity_id", "vendor", "currency",
                          "amount_local", "issue_date", "due_date", "status"],
    "tax_obligations":   ["entity_id", "jurisdiction", "tax_type", "period",
                          "amount_local", "currency", "due_date", "status"],
}

# Richer canonical tables (not consumed by finance_core; carried in the snapshot
# and exposed read-only over MCP for traceability).
EXTRA_TABLES = {
    "trial_balance":   ["entity_id", "period", "account_code", "account_name",
                        "debit", "credit", "currency"],
    "payments":        ["payment_id", "entity_id", "party", "party_type",
                        "currency", "amount_local", "txn_date", "applied_to"],
    "customers":       ["customer_id", "entity_id", "name", "currency", "balance"],
    "vendors":         ["vendor_id", "entity_id", "name", "currency", "balance"],
    "journal_entries": ["je_id", "entity_id", "period", "account_code",
                        "debit", "credit", "currency", "txn_date"],
}

ALL_TABLES = {**CONTRACT_TABLES, **EXTRA_TABLES}

# --------------------------------------------------------------------------
# The canonical rollup chart of accounts (matches finance-mcp/data/
# chart_of_accounts.csv exactly). Every source maps into these codes.
# --------------------------------------------------------------------------
CANONICAL_COA = [
    {"account_code": "1000", "account_name": "Cash and equivalents",   "type": "Asset"},
    {"account_code": "1100", "account_name": "Accounts receivable",    "type": "Asset"},
    {"account_code": "1500", "account_name": "Fixed assets, net",      "type": "Asset"},
    {"account_code": "2000", "account_name": "Accounts payable",       "type": "Liability"},
    {"account_code": "2500", "account_name": "Deferred revenue",       "type": "Liability"},
    {"account_code": "3000", "account_name": "Paid-in capital",        "type": "Equity"},
    {"account_code": "3900", "account_name": "Retained earnings",      "type": "Equity"},
    {"account_code": "4000", "account_name": "Revenue",                "type": "Revenue"},
    {"account_code": "5000", "account_name": "Cost of revenue",        "type": "Expense"},
    {"account_code": "6000", "account_name": "Sales & marketing",      "type": "Expense"},
    {"account_code": "6100", "account_name": "Research & development", "type": "Expense"},
    {"account_code": "6200", "account_name": "General & admin",        "type": "Expense"},
]
COA_NAME = {a["account_code"]: a["account_name"] for a in CANONICAL_COA}
COA_TYPE = {a["account_code"]: a["type"] for a in CANONICAL_COA}

# Account-code groupings the engine relies on.
PNL_REVENUE = "4000"
PNL_COGS = "5000"
PNL_SM = "6000"
PNL_RD = "6100"
PNL_GA = "6200"
PNL_EXPENSE_CODES = (PNL_SM, PNL_RD, PNL_GA)

BS_CASH = "1000"
BS_AR = "1100"
BS_FIXED = "1500"
BS_AP = "2000"
BS_DEFERRED = "2500"
BS_PAID_IN = "3000"
BS_RETAINED = "3900"
BS_ASSET_CODES = (BS_CASH, BS_AR, BS_FIXED)
BS_LIAB_CODES = (BS_AP, BS_DEFERRED)
BS_EQUITY_CODES = (BS_PAID_IN, BS_RETAINED)

# Valid AR/AP statuses in the canonical (matches the synthetic data).
OPEN, PAID = "open", "paid"


def empty_table(name):
    """A header-only canonical table (used when a source has no rows for it,
    e.g. QuickBooks sandbox has no budget or tax-obligation object)."""
    return []


def contract_columns(name):
    return list(CONTRACT_TABLES[name])
