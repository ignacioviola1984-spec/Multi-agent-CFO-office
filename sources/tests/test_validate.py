"""test_validate.py - deterministic validations pass on clean data and fail on tampered data."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
from _fixture import build_canonical, PERIOD
import validate as V


def _check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


class ValidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_clean_data_passes_all_checks(self):
        r = V.validate_canonical(self.c, PERIOD)
        self.assertTrue(r["pass"], [c for c in r["checks"] if not c["ok"]])
        self.assertTrue(all(c["ok"] for c in r["checks"]))
        self.assertGreater(r["record_counts"]["pnl_activity"], 0)

    def test_unbalanced_balance_sheet_fails(self):
        c = copy.deepcopy(self.c)
        for row in c["balance_sheet"]:
            if row["account_code"] == "1000":
                row["amount_local"] = 999999.0
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(r["pass"])
        self.assertFalse(_check(r, "balance_sheet_foots")["ok"])

    def test_ar_not_tying_to_control_fails(self):
        c = copy.deepcopy(self.c)
        for row in c["balance_sheet"]:
            if row["account_code"] == "1100":
                row["amount_local"] = 12345.0
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "ar_subledger_ties_to_control")["ok"])

    def test_future_dated_posting_fails(self):
        c = copy.deepcopy(self.c)
        c["ar_invoices"].append({"invoice_id": "INV-999", "entity_id": "US", "customer": "X",
                                 "currency": "USD", "amount_local": 1.0,
                                 "issue_date": "2026-09-01", "due_date": "2026-09-30", "status": "open"})
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "no_future_dated_postings")["ok"])

    def test_unknown_currency_fails(self):
        c = copy.deepcopy(self.c)
        c["ar_invoices"][0]["currency"] = "EUR"
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "currency_present_and_known")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
