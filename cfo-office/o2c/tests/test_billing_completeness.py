"""test_billing_completeness.py - billing completeness catches unbilled work."""

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


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


class BillingCompletenessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()

    def test_unbilled_revenue_detected(self):
        unb = core.calculate_unbilled_revenue(self.dfs, "2026-05")
        self.assertGreaterEqual(unb["unbilled_count"], 10)         # seeded 12
        self.assertGreater(unb["unbilled_amount_usd"], 0)

    def test_control_d_fails_and_matches_core(self):
        unb = core.calculate_unbilled_revenue(self.dfs, "2026-05")
        r = controls.ctl_billing_completeness(self.dfs, "2026-05")
        self.assertEqual(r.status, "FAIL")
        self.assertTrue(r.blocks_reporting)
        self.assertEqual(r.failing_record_count, unb["unbilled_count"])

    def test_unbilled_lines_are_billable_due_and_uninvoiced(self):
        # independent re-derivation: every flagged line is billable, due, no invoice
        chain = core.build_opportunity_to_cash_chain(self.dfs, "2026-05")
        due = chain["due_billing"]
        unb = core.calculate_unbilled_revenue(self.dfs, "2026-05")
        flagged = set(unb["unbilled"]["billing_schedule_id"])
        sub = due[due["billing_schedule_id"].isin(flagged)]
        self.assertTrue((sub["billing_status"] == "billable").all())
        self.assertTrue((~sub["has_invoice"]).all())

    def test_completeness_pct_below_100(self):
        comp = core.calculate_billing_completeness(self.dfs, "2026-05")
        self.assertLess(comp["billing_completeness_pct"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
