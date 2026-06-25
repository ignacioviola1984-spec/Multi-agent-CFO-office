"""test_mapper.py - QuickBooks -> canonical mapping is correct and leaks no vendor names."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
from _fixture import build_canonical


def _by_code(rows):
    return {r["account_code"]: float(r["amount_local"]) for r in rows}


class MapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_pnl_rolls_up_into_canonical_codes(self):
        pnl = _by_code(self.c["pnl_activity"])
        self.assertEqual(pnl, {"4000": 50000.0, "5000": 18000.0,
                               "6000": 5000.0, "6100": 8000.0, "6200": 12000.0})

    def test_balance_sheet_rolls_up_and_foots(self):
        bs = _by_code(self.c["balance_sheet"])
        self.assertEqual(bs, {"1000": 80000.0, "1100": 30000.0, "1500": 20000.0,
                              "2000": 15000.0, "2500": 10000.0,
                              "3000": 50000.0, "3900": 55000.0})
        assets = bs["1000"] + bs["1100"] + bs["1500"]
        liab_eq = bs["2000"] + bs["2500"] + bs["3000"] + bs["3900"]
        self.assertEqual(assets, liab_eq)

    def test_ar_open_ties_to_control(self):
        ar = self.c["ar_invoices"]
        self.assertEqual(len(ar), 4)
        open_ar = sum(float(r["amount_local"]) for r in ar if r["status"] == "open")
        self.assertEqual(open_ar, 30000.0)
        self.assertEqual(sum(1 for r in ar if r["status"] == "paid"), 1)

    def test_ap_open_ties_to_control(self):
        ap = self.c["ap_invoices"]
        open_ap = sum(float(r["amount_local"]) for r in ap if r["status"] == "open")
        self.assertEqual(open_ap, 15000.0)

    def test_chart_of_accounts_is_canonical_rollup(self):
        codes = {a["account_code"] for a in self.c["chart_of_accounts"]}
        self.assertEqual(codes, {"1000", "1100", "1500", "2000", "2500", "3000",
                                 "3900", "4000", "5000", "6000", "6100", "6200"})

    def test_trial_balance_balances(self):
        tb = self.c["trial_balance"]
        debits = round(sum(float(r["debit"]) for r in tb), 2)
        credits = round(sum(float(r["credit"]) for r in tb), 2)
        self.assertEqual(debits, credits)

    def test_no_quickbooks_names_leak_into_engine_tables(self):
        # The canonical tables the engine reads must carry codes, never QBO names.
        leak = []
        for r in self.c["pnl_activity"] + self.c["balance_sheet"]:
            if any(s in str(r.values()).lower() for s in ("sales", "advertis", "checking", "truck")):
                leak.append(r)
        self.assertEqual(leak, [])

    def test_account_routing(self):
        import mapper
        self.assertEqual(mapper.canonical_code_for_account(
            {"Name": "Checking", "AccountType": "Bank"}), "1000")
        self.assertEqual(mapper.canonical_code_for_account(
            {"Name": "Unearned Revenue", "AccountType": "Other Current Liability"}), "2500")
        self.assertEqual(mapper.canonical_code_for_account(
            {"Name": "Advertising", "AccountType": "Expense"}), "6000")
        self.assertEqual(mapper.canonical_code_for_account(
            {"Name": "Retained Earnings", "AccountType": "Equity"}), "3900")


if __name__ == "__main__":
    unittest.main(verbosity=2)
