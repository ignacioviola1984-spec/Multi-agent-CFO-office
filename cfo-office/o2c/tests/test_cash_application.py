"""test_cash_application.py - cash application catches unapplied cash and ties out."""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
for _p in (O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader
import o2c_core as core
import o2c_controls as controls
import cash_application_agent
from base_agent import O2CContext


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


class CashApplicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()

    def test_unapplied_cash_detected(self):
        u = core.calculate_unapplied_cash(self.dfs, "2026-05")
        self.assertGreaterEqual(u["unapplied_count"], 10)          # seeded 14
        self.assertGreater(u["unapplied_cash_usd"], 0)

    def test_control_j_fails_on_undocumented_unapplied(self):
        r = controls.ctl_cash_application_completeness(self.dfs, "2026-05")
        self.assertEqual(r.status, "FAIL")
        self.assertGreaterEqual(r.failing_record_count, 5)         # seeded 5 with no reason
        self.assertTrue(r.blocks_reporting)

    def test_payments_without_bank_receipt_detected(self):
        r = controls.ctl_cash_receipt_to_bank(self.dfs, "2026-05")
        self.assertEqual(r.status, "FAIL")
        self.assertGreaterEqual(r.failing_record_count, 5)         # seeded 8

    def test_treasury_tie_out_balances(self):
        # received = applied + unapplied(unmatched), within tolerance
        capp = core.calculate_cash_application_status(self.dfs, "2026-05")
        unapp = core.calculate_unapplied_cash(self.dfs, "2026-05")
        diff = capp["received_usd"] - capp["applied_usd"] - unapp["unmatched_receipt_usd"]
        self.assertLessEqual(abs(diff), max(1.0, capp["received_usd"] * 0.001))


if __name__ == "__main__":
    unittest.main(verbosity=2)
