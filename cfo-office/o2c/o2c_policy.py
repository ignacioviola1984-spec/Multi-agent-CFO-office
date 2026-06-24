"""
o2c_policy.py - Configurable O2C / Order-to-Cash policy.

Single source of truth for the thresholds, materiality, owners, and the
maker/checker (HITL) map used across the control tower. Nothing here computes a
business number; this is the policy a Controller would set and a board would
approve. Controls and metrics read these values so a threshold change is a
one-line edit, not a code change scattered across modules.

All amounts are in USD (the reporting currency) unless a field says otherwise.
"""

# --------------------------------------------------------------------------
# Reporting context
# --------------------------------------------------------------------------
DEFAULT_PERIOD = "2026-05"          # YYYY-MM; the as-of reporting month
REPORTING_CURRENCY = "USD"

# Static FX rates to USD as of the reporting period. Deterministic on purpose:
# a backtest or a re-run must reproduce the same USD-normalized figures. In a
# real deployment these come from the treasury rate table for the period close.
FX_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "BRL": 0.20,
    "MXN": 0.058,
    "ARS": 0.0011,
}
CURRENCIES = tuple(FX_TO_USD.keys())

# --------------------------------------------------------------------------
# Tolerances and thresholds (the policy a Controller sets)
# --------------------------------------------------------------------------
INVOICE_AMOUNT_TOLERANCE = 0.01           # invoice vs scheduled bill: 1% relative
INVOICE_AMOUNT_TOLERANCE_USD = 25.0       # ...with an absolute USD floor
MAX_INVOICE_DELAY_DAYS = 5                # invoice issued > N days after scheduled = late

DSO_WARNING_THRESHOLD = 45.0
DSO_URGENT_THRESHOLD = 60.0
DSO_CRITICAL_THRESHOLD = 75.0

UNAPPLIED_CASH_WARNING_PCT = 5.0          # unapplied cash as % of cash received
DISPUTED_AR_WARNING_PCT = 8.0             # disputed AR as % of open AR
CREDIT_LIMIT_UTILIZATION_WARNING_PCT = 90.0
BROKEN_PROMISE_URGENT_DAYS = 7            # promise-to-pay past due by > N days = urgent

REVENUE_RECOGNITION_TOLERANCE = 0.01      # recognized vs scheduled: 1% relative
DEFERRED_REVENUE_ROLLFORWARD_TOLERANCE = 1.0   # USD; rollforward must foot to <= this
MINIMUM_CASH_APPLICATION_RATE = 95.0      # % of receipts that must be applied
AR_TO_GL_TOLERANCE_USD = 1.0              # subledger vs GL control account

MATERIALITY_THRESHOLD_USD = 25000.0       # below this, an exception is informational

# Soft-control thresholds
BILLING_TIMELINESS_WARNING_PCT = 90.0     # % of invoices on time; below = warning
AGING_CONCENTRATION_WARNING_PCT = 25.0    # % of open AR in 90+ days; above = warning
HIGH_UNAPPLIED_CASH_WARNING_PCT = 5.0     # alias for the soft control
HIGH_DISPUTE_RATE_WARNING_PCT = 8.0
MANUAL_CREDIT_MEMO_WARNING_PCT = 10.0     # credit memos as % of invoice count
STALE_CREDIT_REVIEW_DAYS = 365            # customer credit review older than this = stale
FX_GAIN_LOSS_WARNING_USD = 10000.0        # absolute FX gain/loss above this = warning

# Standard commercial terms; anything else is "non-standard" and flagged soft.
STANDARD_PAYMENT_TERMS = ("NET15", "NET30", "NET45", "NET60")

# AR aging buckets, as (lower_days_inclusive, label). "current" = not yet due.
AGING_BUCKETS = (
    (-10_000, "current"),
    (1, "1-30"),
    (31, "31-60"),
    (61, "61-90"),
    (91, "91-120"),
    (121, "120+"),
)

# --------------------------------------------------------------------------
# Ownership: who owns the number (maker) and who signs it off (checker / HITL).
# Mirrors cfo-office/review.py: the agent is the MAKER, the role is the CHECKER.
# --------------------------------------------------------------------------
CHECKER_OWNERS = {
    "OrderIntakeAgent": "RevOps Lead",
    "CustomerMasterAgent": "Finance Operations Manager",
    "ContractAgent": "Revenue Operations Manager",
    "BillingAgent": "Billing Manager",
    "RevenueRecognitionAgent": "Revenue Accounting Manager",
    "CollectionsAgent": "Collections Manager",
    "CashApplicationAgent": "Treasury / AR Manager",
    "DisputesCreditAgent": "Credit & Commercial Finance Manager",
    "RevOpsAnalyticsAgent": "FP&A Director",
    "O2CAuditAgent": "Controller / Internal Controls",
}

# Each agent's maker (the function that produces the work).
MAKER_OWNERS = {
    "OrderIntakeAgent": "Order Intake / RevOps",
    "CustomerMasterAgent": "Finance Operations",
    "ContractAgent": "Revenue Operations",
    "BillingAgent": "Billing Operations",
    "RevenueRecognitionAgent": "Revenue Accounting",
    "CollectionsAgent": "Collections",
    "CashApplicationAgent": "Cash Application / Treasury",
    "DisputesCreditAgent": "Credit & Disputes",
    "RevOpsAnalyticsAgent": "Revenue Operations Analytics",
    "O2CAuditAgent": "Internal Audit",
}

# Owner used on each metric (who is accountable for moving it).
METRIC_OWNERS = {
    "bookings": "Revenue Operations",
    "billing": "Billing Operations",
    "revenue": "Revenue Accounting",
    "ar": "Collections",
    "cash": "Cash Application / Treasury",
    "credit": "Credit & Commercial Finance",
    "controls": "Internal Controls",
}

# Escalation routing for serious findings.
ESCALATION_PATH = {
    "HARD_CONTROL": "Controller -> CFO (blocks reporting)",
    "CREDIT": "Credit & Commercial Finance -> CFO",
    "COLLECTIONS": "Collections Manager -> Sales -> Legal",
    "REVENUE": "Revenue Accounting Manager -> Controller",
    "DISPUTE": "Dispute owner team -> Commercial Finance",
}


def status_for_dso(dso):
    """Map a DSO value to a policy status band."""
    if dso >= DSO_CRITICAL_THRESHOLD:
        return "CRITICAL"
    if dso >= DSO_URGENT_THRESHOLD:
        return "URGENT"
    if dso >= DSO_WARNING_THRESHOLD:
        return "REVIEW"
    return "OK"


def invoice_tolerance_usd(scheduled_amount_usd):
    """Allowed absolute USD difference between invoice and scheduled bill."""
    return max(INVOICE_AMOUNT_TOLERANCE_USD, abs(scheduled_amount_usd) * INVOICE_AMOUNT_TOLERANCE)
