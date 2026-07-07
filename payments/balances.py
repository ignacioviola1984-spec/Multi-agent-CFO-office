"""
payments/balances.py - Reconciled balances, read from the CANONICAL layer.

The sufficient-funds check must tie to reconciled canonical state, NOT to a live
API read of a bank/wallet (which can be racy, unreconciled, or spoofed). This
reader loads the canonical `cash_bank` table (the same table the event replay and
the batch connectors emit) and exposes the available balance per source account.

Because it reads canonical CSVs produced by the ingestion + validation pipeline,
"sufficient balance" means "reconciled balance", by construction. No network call
lives here.
"""

import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANONICAL = os.path.join(ROOT, "sources", "canonical")
if CANONICAL not in sys.path:
    sys.path.insert(0, CANONICAL)

import csvio  # noqa: E402


class ReconciledBalances:
    """Available balance per source account, from a canonical data directory.

    `data_dir` points at a canonical directory containing cash_bank.csv (e.g. the
    output of sources/events/replay.py, or any canonical snapshot). If the table is
    absent, every balance is zero -- fail closed, never assume funds."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self._by_account = {}
        self._load()

    def _load(self):
        rows = csvio.read_table(os.path.join(self.data_dir, "cash_bank.csv"))
        for r in rows:
            self._by_account[r.get("account_id")] = {
                "currency": r.get("currency", ""),
                "balance": Decimal(str(r.get("balance") or "0")),
            }

    def account(self, source_account):
        return self._by_account.get(source_account)

    def available(self, source_account):
        rec = self._by_account.get(source_account)
        return rec["balance"] if rec else Decimal("0")

    def currency(self, source_account):
        rec = self._by_account.get(source_account)
        return rec["currency"] if rec else None

    def known(self, source_account):
        return source_account in self._by_account
