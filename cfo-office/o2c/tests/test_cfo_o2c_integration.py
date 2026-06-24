"""
test_cfo_o2c_integration.py - the O2C tower runs as a sub-orchestration under the
CFO orchestrator and folds its result into the shared CFO state.

Exercises the deterministic bridge (cfo-office/cfo_o2c_bridge.py) and the shared
CFOContext directly, so it needs no ANTHROPIC_API_KEY (the close agents' LLM calls
are not on this path).
"""

import os
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
CFO_OFFICE = os.path.dirname(O2C)
for _p in (O2C, os.path.join(O2C, "agents"), CFO_OFFICE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader
import cfo_o2c_bridge as bridge
from shared_state import CFOContext


def _ensure_data():
    if not os.path.exists(os.path.join(loader.period_data_dir("2026-05"), "invoices.csv")):
        import generate_data
        generate_data.generate_all()


class CfoO2cIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()

    def test_blocked_period_folds_into_cfo_state(self):
        out = tempfile.mkdtemp(prefix="cfo_o2c_05_")
        ctx = CFOContext()
        r = bridge.run_o2c_suborchestration(ctx, "2026-05", output_dir=out)
        self.assertEqual(r["status"], "BLOCKED_HARD_CONTROLS")
        self.assertGreaterEqual(r["hard_failures"], 13)
        self.assertEqual(r["audit_opinion"], "adverse")
        # folded into the shared CFO state under "Order-to-Cash"
        self.assertEqual(ctx.get("Order-to-Cash", "status"), "BLOCKED_HARD_CONTROLS")
        sevs = [e[0] for e in ctx.get("Order-to-Cash", "escalations", [])]
        self.assertIn("CRITICAL", sevs)
        self.assertIn("Order-to-Cash", ctx.get("Order-to-Cash", "section", ""))
        # the sub-orchestration is recorded in the CFO audit trail
        actors = [e["agent"] for e in ctx.state["audit"]]
        self.assertIn("Order-to-Cash", actors)

    def test_clean_period_no_critical(self):
        out = tempfile.mkdtemp(prefix="cfo_o2c_06_")
        ctx = CFOContext()
        r = bridge.run_o2c_suborchestration(ctx, "2026-06", output_dir=out)
        self.assertEqual(r["status"], "PASS_WITH_WARNINGS")
        self.assertEqual(r["hard_failures"], 0)
        self.assertEqual(r["audit_opinion"], "unqualified")
        sevs = [e[0] for e in ctx.get("Order-to-Cash", "escalations", [])]
        self.assertNotIn("CRITICAL", sevs)

    def test_wired_into_cfo_orchestrator(self):
        # the CFO orchestrator imports the bridge, runs it, and lists Order-to-Cash
        # as a consolidated agent (text check; avoids importing the LLM client).
        src = open(os.path.join(CFO_OFFICE, "cfo_orchestrator.py"), encoding="utf-8").read()
        self.assertIn("import cfo_o2c_bridge", src)
        self.assertIn("run_o2c_suborchestration", src)
        self.assertIn('"Order-to-Cash"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
