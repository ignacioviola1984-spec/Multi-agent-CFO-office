"""test_collections_risk.py - risk scoring, credit breach, and orchestrator outputs."""

import os
import sys
import tempfile
import unittest

import pandas as pd

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
for _p in (O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader
import o2c_core as core
import o2c_controls as controls
from collections_agent import score_collections_risk
import o2c_orchestrator as orch


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


class CollectionsRiskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()

    def test_risk_score_ranks_highest_risk_first(self):
        open_items = pd.DataFrame([
            {"customer_id": "C-HIGH", "invoice_id": "i1", "open_usd": 100000.0,
             "days_overdue": 120, "is_disputed": False},
            {"customer_id": "C-HIGH", "invoice_id": "i2", "open_usd": 50000.0,
             "days_overdue": 90, "is_disputed": False},
            {"customer_id": "C-MID", "invoice_id": "i3", "open_usd": 30000.0,
             "days_overdue": 40, "is_disputed": False},
            {"customer_id": "C-LOW", "invoice_id": "i4", "open_usd": 5000.0,
             "days_overdue": 10, "is_disputed": False},
            {"customer_id": "C-LOW", "invoice_id": "i5", "open_usd": 999999.0,
             "days_overdue": 200, "is_disputed": True},   # disputed -> excluded
        ])
        customers = pd.DataFrame([
            {"customer_id": "C-HIGH", "risk_tier": "high"},
            {"customer_id": "C-MID", "risk_tier": "medium"},
            {"customer_id": "C-LOW", "risk_tier": "low"},
        ])
        ranking = score_collections_risk(open_items, customers)
        order = list(ranking["customer_id"])
        self.assertEqual(order[0], "C-HIGH")
        self.assertEqual(order, ["C-HIGH", "C-MID", "C-LOW"])
        # scores strictly descending
        scores = list(ranking["risk_score"])
        self.assertEqual(scores, sorted(scores, reverse=True))
        # the disputed invoice did not inflate C-LOW
        low = ranking[ranking["customer_id"] == "C-LOW"].iloc[0]
        self.assertEqual(low["overdue_usd"], 5000.0)

    def test_real_ranking_is_sorted(self):
        items = core.calculate_ar_open_items(self.dfs, "2026-05")
        ranking = score_collections_risk(items, self.dfs["customers"])
        scores = list(ranking["risk_score"])
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(len(ranking), 0)

    def test_credit_limit_breach_detected(self):
        ce = core.calculate_credit_exposure(self.dfs, "2026-05")
        self.assertGreaterEqual(ce["breach_count"], 10)            # seeded 10
        r = controls.ctl_credit_limit_breach(self.dfs, "2026-05")
        self.assertEqual(r.status, "FAIL")
        self.assertTrue(r.blocks_reporting)
        # every flagged customer truly exceeds its limit
        b = ce["breaches"]
        self.assertTrue((b["current_exposure_amount_usd"] > b["credit_limit_usd"]).all())

    def test_orchestrator_generates_all_outputs(self):
        out = tempfile.mkdtemp(prefix="o2c_out_")
        ctx, meta = orch.run(period="2026-05", output_dir=out, fail_on_hard=False, verbose=False)
        expected = ["o2c_control_results.csv", "o2c_metrics.csv", "o2c_exceptions.csv",
                    "o2c_agent_findings.json", "o2c_audit_trail.json",
                    "o2c_executive_summary.md", "o2c_board_pack.md", "o2c_workflow_map.md"]
        for fn in expected:
            self.assertTrue(os.path.exists(os.path.join(out, fn)), f"missing output {fn}")
        self.assertEqual(meta["final_status"], "BLOCKED_HARD_CONTROLS")
        self.assertEqual(meta["audit_opinion"], "adverse")


if __name__ == "__main__":
    unittest.main(verbosity=2)
