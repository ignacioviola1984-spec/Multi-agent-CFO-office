"""
payments/policy.py - The payment policy, in CODE (not mutable state).

Like the self-improvement registry bounds and AUTO_ADOPT_ENABLED, the numbers and
lists that constrain a payment live in code, so nothing at runtime can widen a
limit or add a counterparty by writing to a data store. Two things live here:

  * Limits: per-transaction and per-period (cumulative per source account), in the
    account's NATIVE currency. Currency consistency (enforced in validation) means
    no FX is needed to apply a limit, so the check stays deterministic.

  * The allowlisted counterparty registry: the ONLY destinations a payment may go
    to. Each entry pins the counterparty's account/address, entity and currency;
    validation requires an exact match, so a payment to an unknown counterparty --
    or to an allowlisted counterparty at the wrong address/currency/entity -- is
    blocked.

  * The required approver role per source account: who must sign a payout from that
    account (bound to an authenticated identity in identity/). This is the "owner"
    the maker-checker gate authorizes against.
"""

from decimal import Decimal

# --- limits (native currency of the source account) --------------------------
PER_TRANSACTION_LIMIT = Decimal("100000.00")
PER_PERIOD_LIMIT = Decimal("250000.00")     # cumulative per source account, per period

# --- allowlisted counterparties (the ONLY legal destinations) ----------------
# id -> pinned destination. `account` is the IBAN (bank) or address (wallet).
ALLOWLIST = {
    "cp-acme-bank": {
        "name": "ACME Corp",
        "type": "bank_account",
        "account": "US64SVBKUS6S3300958879",
        "entity_id": "US",
        "currency": "USD",
    },
    "cp-vendorx-wallet": {
        "name": "Vendor X",
        "type": "wallet",
        "account": "0xVENDORX00000000000000000000000000000001",
        "entity_id": "US",
        "currency": "USD",
    },
    "cp-payroll-bank": {
        "name": "Payroll Provider",
        "type": "bank_account",
        "account": "GB29NWBK60161331926819",
        "entity_id": "US",
        "currency": "USD",
    },
}

# --- required approver role per source account (the maker-checker "owner") ----
# A payout from a source account must be signed by an identity holding this role.
# The proposer can never be the approver (segregation of duties, identity/).
SOURCE_ACCOUNT_APPROVER = {
    "wlt-usd-01": "Controller",
    "bank-usd-01": "Controller",
}
DEFAULT_APPROVER_ROLE = "CFO"


def counterparty(counterparty_id):
    return ALLOWLIST.get(counterparty_id)


def required_approver_role(source_account):
    return SOURCE_ACCOUNT_APPROVER.get(source_account, DEFAULT_APPROVER_ROLE)
