"""test_revenue_recognition.py - cutoff and deferred-rollforward breaks are caught."""

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


class RevenueRecognitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()

    def test_cutoff_exceptions_detected(self):
        rr = core.calculate_revenue_recognition_rollforward(self.dfs, "2026-05")
        self.assertGreaterEqual(rr["cutoff_exception_count"], 8)   # seeded 8 before-start
        # at least one is a 'before service start' exception
        cut = rr["cutoff_exceptions"]
        self.assertTrue(bool(cut["before_service_start"].any()))

    def test_control_k_fails(self):
        r = controls.ctl_revenue_recognition_cutoff(self.dfs, "2026-05")
        self.assertEqual(r.status, "FAIL")
        self.assertTrue(r.blocks_reporting)

    def test_deferred_rollforward_breaks_detected(self):
        dr = core.calculate_deferred_revenue_rollforward(self.dfs, "2026-05")
        self.assertEqual(dr["rollforward_break_count"], 6)         # seeded 6
        # every flagged break genuinely does not foot
        breaks = dr["breaks"]
        self.assertTrue((breaks["foot_diff"].abs() > 1.0).all())

    def test_clean_rows_foot(self):
        # the non-broken rows must foot exactly (closing == expected)
        d = self.dfs["deferred"].copy()
        d["expected"] = (d["opening_deferred_revenue"] + d["billings"] - d["recognized_revenue"]
                         + d["adjustments"] + d["fx_impact"]).round(2)
        d["diff"] = (d["closing_deferred_revenue"] - d["expected"]).round(2)
        n_break = int((d["diff"].abs() > 1.0).sum())
        self.assertEqual(n_break, 6)                               # exactly the seeded breaks


if __name__ == "__main__":
    unittest.main(verbosity=2)
