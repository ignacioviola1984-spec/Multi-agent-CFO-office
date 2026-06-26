"""test_erpnext_validate.py - the SHARED validations pass on the clean multi-
company / multi-currency ERPNext canonical and fail on tampered copies (per
entity, per currency). Offline against the recorded fixture."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext
from _fixture_erpnext import build_canonical, PERIOD
import validate as V


def _check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


class ErpNextValidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_clean_multi_entity_passes_all_checks(self):
        r = V.validate_canonical(self.c, PERIOD)
        self.assertTrue(r["pass"], [c for c in r["checks"] if not c["ok"]])
        # the multi-entity / multi-currency checks are the ones QuickBooks could not exercise
        self.assertTrue(_check(r, "balance_sheet_foots")["ok"])
        self.assertTrue(_check(r, "fx_rates_cover_currencies")["ok"])

    def test_one_entity_not_footing_fails(self):
        c = copy.deepcopy(self.c)
        for row in c["balance_sheet"]:
            if row["entity_id"] == "LUK" and row["account_code"] == "1000":
                row["amount_local"] = 999999.0
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(r["pass"])
        self.assertFalse(_check(r, "balance_sheet_foots")["ok"])

    def test_one_entity_ar_not_tying_fails(self):
        c = copy.deepcopy(self.c)
        for row in c["balance_sheet"]:
            if row["entity_id"] == "LUS" and row["account_code"] == "1100":
                row["amount_local"] = 12345.0
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "ar_subledger_ties_to_control")["ok"])

    def test_currency_without_fx_rate_fails_both_currency_and_fx_coverage(self):
        c = copy.deepcopy(self.c)
        c["ar_invoices"][0]["currency"] = "JPY"   # no fx_rates row for JPY
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "currency_present_and_known")["ok"])
        self.assertFalse(_check(r, "fx_rates_cover_currencies")["ok"])

    def test_entity_currency_without_fx_fails_coverage(self):
        c = copy.deepcopy(self.c)
        c["fx_rates"] = [r for r in c["fx_rates"] if r["currency"] != "GBP"]  # drop GBP rate
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "fx_rates_cover_currencies")["ok"])

    def test_future_dated_posting_fails(self):
        c = copy.deepcopy(self.c)
        c["ar_invoices"].append({"invoice_id": "INV-FUT", "entity_id": "LUS", "customer": "X",
                                 "currency": "USD", "amount_local": 1.0,
                                 "issue_date": "2026-09-01", "due_date": "2026-09-30", "status": "open"})
        r = V.validate_canonical(c, PERIOD)
        self.assertFalse(_check(r, "no_future_dated_postings")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
