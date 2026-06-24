"""test_o2c_controls.py - the control framework catches the seeded exceptions."""

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


class ControlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()
        cls.results = controls.run_all_controls(cls.dfs, "2026-05")
        cls.by_id = {r.control_id: r for r in cls.results}

    def test_fifteen_hard_and_ten_soft(self):
        hard = [r for r in self.results if r.severity == "HARD"]
        soft = [r for r in self.results if r.severity == "SOFT"]
        self.assertEqual(len(hard), 15)
        self.assertEqual(len(soft), 10)

    def test_hard_failures_block_reporting(self):
        summ = controls.controls_summary(self.results)
        self.assertTrue(summ["blocks_reporting"])
        self.assertGreaterEqual(summ["hard_failures"], 13)

    def test_each_seeded_hard_control_fails(self):
        must_fail = ["A_CRM_CLOSED_WON_TO_CONTRACT", "B_CONTRACT_TO_ORDER",
                     "C_ORDER_TO_BILLING_SCHEDULE", "D_BILLING_COMPLETENESS",
                     "E_INVOICE_ACCURACY", "F_PO_REQUIRED_CONTROL",
                     "G_INVOICE_DUPLICATE_CONTROL", "H_AR_SUBLEDGER_COMPLETENESS",
                     "I_CASH_RECEIPT_TO_BANK", "J_CASH_APPLICATION_COMPLETENESS",
                     "K_REVENUE_RECOGNITION_CUTOFF", "L_DEFERRED_REVENUE_ROLLFORWARD",
                     "M_CREDIT_LIMIT_BREACH", "N_CREDIT_HOLD_NEW_ORDER_BLOCK"]
        for cid in must_fail:
            r = self.by_id[cid]
            self.assertEqual(r.status, "FAIL", f"{cid} should FAIL")
            self.assertTrue(r.blocks_reporting)
            self.assertGreater(r.failing_record_count, 0)

    def test_segregation_control_passes(self):
        # disputed cash is routed out of the forecast; this control should PASS
        self.assertEqual(self.by_id["O_DISPUTE_COLLECTION_BLOCK"].status, "PASS")

    def test_control_results_are_traceable(self):
        for r in self.results:
            self.assertTrue(r.owner and r.checker, f"{r.control_id} missing owner/checker")
            self.assertTrue(r.source_tables, f"{r.control_id} missing source tables")
            self.assertIn(r.severity, ("HARD", "SOFT"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
