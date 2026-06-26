"""test_erpnext_engine_end_to_end.py - the multi-entity / multi-currency swap
actually works: ERPNext fixture -> canonical -> finance_core CONSOLIDATES two
companies (USD + GBP) into one USD close, with the SAME engine code and zero
ERPNext awareness. This is the consolidation path the QuickBooks sandbox could
not exercise. finance_core runs in a subprocess so FINANCE_DATA_DIR is isolated.

Fixture (document currency, GBP->USD at units_per_usd 0.80):
  Lumen US Inc. (USD): revenue 100,000  operating income 15,000  cash 120,000
  Lumen UK Ltd. (GBP): revenue  80,000  operating income 20,000  cash  96,000
Consolidated (USD): revenue 200,000  operating income 40,000  cash 240,000;
balance sheet foots (A = L + E)."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext
from _fixture_erpnext import build_canonical, REPO
import materialize

ORCH = os.path.join(REPO, "orchestration")
SCRIPT = (
    "import sys, os\n"
    f"sys.path.insert(0, {ORCH!r})\n"
    "import finance_core as fc\n"
    "p = fc.pnl_usd('2026-05'); bs = fc.balance_sheet_statement('2026-05')\n"
    "print(round(p['revenue'],2), round(p['operating_income'],2), "
    "round(bs['balance_check'],2), round(fc.cash_total_usd('2026-05'),2))\n"
)


def _run(env_extra):
    env = dict(os.environ)
    env.pop("FINANCE_DATA_DIR", None)
    env.pop("FINANCE_LATEST_PERIOD", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"finance_core subprocess failed: {out.stderr}")
    return out.stdout.strip().split()


class ErpNextEngineEndToEndTest(unittest.TestCase):
    def test_erpnext_canonical_consolidates_through_finance_core(self):
        out_dir = tempfile.mkdtemp(prefix="erp_canon_")
        materialize.write_canonical_tables(build_canonical(), out_dir)
        revenue, oi, balance_check, cash = _run(
            {"FINANCE_DATA_DIR": out_dir, "FINANCE_LATEST_PERIOD": "2026-05"})
        self.assertEqual(float(revenue), 200000.0)        # 100,000 USD + 80,000 GBP / 0.80
        self.assertEqual(float(oi), 40000.0)              # 15,000 USD + 20,000 GBP / 0.80
        self.assertEqual(float(balance_check), 0.0)       # consolidated A = L + E
        self.assertEqual(float(cash), 240000.0)           # 120,000 USD + 96,000 GBP / 0.80

    def test_default_synthetic_path_is_unchanged(self):
        revenue, _oi, _bc, _cash = _run({})
        self.assertNotEqual(float(revenue), 200000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
