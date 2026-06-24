"""
o2c_data_loader.py - Load, validate, and currency-normalize the O2C datasets.

This is the boundary between raw CSVs and the deterministic calculation layer.
It enforces the schema contract (so a malformed extract fails loudly here, not
deep inside a control) and adds USD-normalized amount columns so multi-currency
data can be consolidated. No business logic lives here.
"""

import os

import pandas as pd

try:
    import o2c_policy as P
except ImportError:                                   # pragma: no cover
    from . import o2c_policy as P

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Logical name -> file. The logical name is what the rest of the system uses.
FILES = {
    "customers": "customer_master.csv",
    "opportunities": "crm_opportunities.csv",
    "contracts": "contracts.csv",
    "orders": "sales_orders.csv",
    "billing": "billing_schedule.csv",
    "invoices": "invoices.csv",
    "credit_memos": "credit_memos.csv",
    "payments": "payments.csv",
    "bank_receipts": "bank_receipts.csv",
    "cash_application": "cash_application.csv",
    "revenue": "revenue_schedule.csv",
    "deferred": "deferred_revenue_rollforward.csv",
    "collections": "collections_activity.csv",
    "disputes": "disputes.csv",
    "credit_limits": "credit_limits.csv",
}

# The schema contract: required columns per table (mirrors the generator).
EXPECTED_COLUMNS = {
    "customers": ["customer_id", "customer_name", "parent_customer_id", "region", "country",
                  "legal_entity", "customer_segment", "customer_status", "default_currency",
                  "payment_terms", "credit_limit", "credit_status", "tax_profile",
                  "po_required_flag", "sales_owner", "revops_owner", "collections_owner",
                  "risk_tier", "created_date", "last_review_date"],
    "opportunities": ["opportunity_id", "customer_id", "opportunity_name", "stage", "close_date",
                      "expected_close_date", "amount", "arr_amount", "currency", "product_line",
                      "sales_owner", "probability", "legal_entity", "billing_model",
                      "billing_frequency", "payment_terms", "contract_start_date",
                      "contract_end_date", "closed_won_flag", "source_system", "last_updated_at"],
    "contracts": ["contract_id", "opportunity_id", "customer_id", "signed_date",
                  "contract_start_date", "contract_end_date", "contract_value", "arr_amount",
                  "currency", "legal_entity", "billing_model", "billing_frequency",
                  "revenue_recognition_method", "performance_obligation_count", "payment_terms",
                  "auto_renew_flag", "po_required_flag", "non_standard_terms_flag",
                  "contract_status", "source_system"],
    "orders": ["order_id", "contract_id", "opportunity_id", "customer_id", "order_date",
               "service_start_date", "service_end_date", "order_amount", "currency",
               "legal_entity", "product_line", "order_status", "billing_block_flag",
               "billing_block_reason", "tax_code", "po_number", "source_system"],
    "billing": ["billing_schedule_id", "contract_id", "order_id", "customer_id",
                "scheduled_invoice_date", "service_period_start", "service_period_end",
                "scheduled_bill_amount", "currency", "billing_status", "invoice_id",
                "billing_exception_reason", "created_at"],
    "invoices": ["invoice_id", "order_id", "contract_id", "customer_id", "invoice_date",
                 "due_date", "invoice_amount", "tax_amount", "total_invoice_amount", "currency",
                 "legal_entity", "invoice_status", "payment_terms", "po_number",
                 "service_period_start", "service_period_end", "gl_ar_account",
                 "gl_revenue_account", "source_system"],
    "credit_memos": ["credit_memo_id", "invoice_id", "customer_id", "credit_date", "credit_amount",
                     "currency", "reason_code", "approved_by", "approval_status", "gl_account",
                     "created_at"],
    "payments": ["payment_id", "customer_id", "payment_date", "payment_amount", "currency",
                 "payment_method", "bank_receipt_id", "remittance_reference", "payer_name",
                 "payment_status", "created_at"],
    "bank_receipts": ["bank_receipt_id", "bank_account_id", "bank_date", "receipt_amount",
                      "currency", "fx_rate_to_usd", "legal_entity", "bank_reference",
                      "source_bank_file", "matched_status", "created_at"],
    "cash_application": ["cash_application_id", "payment_id", "bank_receipt_id", "invoice_id",
                         "customer_id", "applied_date", "applied_amount", "discount_taken",
                         "writeoff_amount", "fx_gain_loss", "application_status",
                         "unapplied_reason", "created_at"],
    "revenue": ["revenue_schedule_id", "contract_id", "invoice_id", "customer_id", "revenue_month",
                "performance_obligation", "recognition_method", "recognized_revenue",
                "deferred_revenue_amount", "currency", "legal_entity", "gl_revenue_account",
                "recognition_status"],
    "deferred": ["period", "customer_id", "contract_id", "legal_entity", "currency",
                 "opening_deferred_revenue", "billings", "recognized_revenue", "adjustments",
                 "fx_impact", "closing_deferred_revenue"],
    "collections": ["collections_activity_id", "invoice_id", "customer_id", "activity_date",
                    "activity_type", "owner", "promise_to_pay_date", "promised_amount", "outcome",
                    "next_step", "escalation_level", "created_at"],
    "disputes": ["dispute_id", "invoice_id", "customer_id", "opened_date", "disputed_amount",
                 "currency", "reason_code", "owner_team", "root_cause", "dispute_status",
                 "expected_resolution_date", "cash_blocked_flag", "created_at"],
    "credit_limits": ["credit_policy_id", "customer_id", "effective_date", "credit_limit",
                      "currency", "current_exposure_amount", "utilization_pct", "credit_status",
                      "hold_flag", "approved_by", "next_review_date", "risk_score"],
}

# Date columns to parse to datetime at load.
DATE_COLUMNS = {
    "customers": ["created_date", "last_review_date"],
    "opportunities": ["close_date", "expected_close_date", "contract_start_date", "contract_end_date"],
    "contracts": ["signed_date", "contract_start_date", "contract_end_date"],
    "orders": ["order_date", "service_start_date", "service_end_date"],
    "billing": ["scheduled_invoice_date", "service_period_start", "service_period_end", "created_at"],
    "invoices": ["invoice_date", "due_date", "service_period_start", "service_period_end"],
    "credit_memos": ["credit_date", "created_at"],
    "payments": ["payment_date", "created_at"],
    "bank_receipts": ["bank_date", "created_at"],
    "cash_application": ["applied_date", "created_at"],
    "collections": ["activity_date", "promise_to_pay_date", "created_at"],
    "disputes": ["opened_date", "expected_resolution_date", "created_at"],
    "credit_limits": ["effective_date", "next_review_date"],
}

# Amount columns to normalize to USD per table, using the row 'currency' column.
USD_COLUMNS = {
    "customers": ([("credit_limit", "credit_limit_usd")], "default_currency"),
    "opportunities": ([("amount", "amount_usd"), ("arr_amount", "arr_amount_usd")], "currency"),
    "contracts": ([("contract_value", "contract_value_usd"), ("arr_amount", "arr_amount_usd")], "currency"),
    "orders": ([("order_amount", "order_amount_usd")], "currency"),
    "billing": ([("scheduled_bill_amount", "scheduled_bill_amount_usd")], "currency"),
    "invoices": ([("invoice_amount", "invoice_amount_usd"),
                  ("tax_amount", "tax_amount_usd"),
                  ("total_invoice_amount", "total_invoice_amount_usd")], "currency"),
    "credit_memos": ([("credit_amount", "credit_amount_usd")], "currency"),
    "payments": ([("payment_amount", "payment_amount_usd")], "currency"),
    "bank_receipts": ([("receipt_amount", "receipt_amount_usd")], "currency"),
    "revenue": ([("recognized_revenue", "recognized_revenue_usd"),
                 ("deferred_revenue_amount", "deferred_revenue_amount_usd")], "currency"),
    "deferred": ([("opening_deferred_revenue", "opening_deferred_revenue_usd"),
                  ("billings", "billings_usd"),
                  ("recognized_revenue", "recognized_revenue_usd"),
                  ("closing_deferred_revenue", "closing_deferred_revenue_usd")], "currency"),
    "disputes": ([("disputed_amount", "disputed_amount_usd")], "currency"),
    "credit_limits": ([("credit_limit", "credit_limit_usd"),
                       ("current_exposure_amount", "current_exposure_amount_usd")], "currency"),
}


class O2CSchemaError(Exception):
    """Raised when a dataset is missing or violates the schema contract."""


def fx_to_usd(currency):
    return P.FX_TO_USD.get(currency, 1.0)


def to_usd(amount_series, currency_series):
    """Vectorized local-currency -> USD using the period FX table."""
    rates = currency_series.map(P.FX_TO_USD).fillna(1.0)
    return (amount_series.astype(float) * rates).round(2)


def load_o2c_data(data_dir=DATA_DIR):
    """Load all 15 datasets into a dict of DataFrames keyed by logical name.

    Parses dates, validates the schema, and adds USD-normalized columns. Raises
    O2CSchemaError with a clear message if a file or a required column is missing.
    """
    if not os.path.isdir(data_dir):
        raise O2CSchemaError(
            f"O2C data directory not found: {data_dir}. "
            f"Run 'python cfo-office/o2c/generate_data.py' to create the datasets.")
    dfs = {}
    for key, fname in FILES.items():
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            raise O2CSchemaError(
                f"Missing dataset '{fname}' (logical '{key}') in {data_dir}. "
                f"Run generate_data.py to (re)create it.")
        df = pd.read_csv(path, dtype={"po_number": str, "parent_customer_id": str})
        dfs[key] = df
    validate_schema(dfs)
    _parse_dates(dfs)
    normalize_currency_amounts(dfs)
    return dfs


def validate_schema(dfs):
    """Confirm every table is present with its required columns."""
    missing_tables = [k for k in EXPECTED_COLUMNS if k not in dfs]
    if missing_tables:
        raise O2CSchemaError(f"missing tables: {missing_tables}")
    for key, required in EXPECTED_COLUMNS.items():
        cols = set(dfs[key].columns)
        missing = [c for c in required if c not in cols]
        if missing:
            raise O2CSchemaError(f"table '{key}' is missing columns: {missing}")
    return True


def _parse_dates(dfs):
    for key, cols in DATE_COLUMNS.items():
        if key not in dfs:
            continue
        for c in cols:
            if c in dfs[key].columns:
                dfs[key][c] = pd.to_datetime(dfs[key][c], errors="coerce")
    return dfs


def normalize_currency_amounts(dfs):
    """Add *_usd columns for every amount field, using the period FX table."""
    for key, (pairs, ccy_col) in USD_COLUMNS.items():
        if key not in dfs or ccy_col not in dfs[key].columns:
            continue
        df = dfs[key]
        for src, dst in pairs:
            if src in df.columns:
                df[dst] = to_usd(df[src], df[ccy_col])
    return dfs


if __name__ == "__main__":
    data = load_o2c_data()
    print(f"Loaded {len(data)} O2C tables from {DATA_DIR}:")
    for k, df in data.items():
        print(f"  {k:18} {len(df):>5} rows  x {len(df.columns):>2} cols")
