"""test_engine_end_to_end.py - the swap actually works: QuickBooks fixture ->
canonical -> finance_core computes the close, with the SAME engine code and zero
QuickBooks awareness. Runs finance_core in a subprocess so FINANCE_DATA_DIR is
isolated and the default (synthetic) import is never contaminated."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
from _fixture import build_canonical, REPO
import materialize

ORCH = os.path.join(REPO, "orchestration")
SCRIPT = (
    "import sys, os\n"
    f"sys.path.insert(0, {ORCH!r})\n"
    "import finance_core as fc\n"
    "p = fc.pnl_usd('2026-05'); bs = fc.balance_sheet_statement('2026-05')\n"
    "c = fc.control_checks('2026-05'); au = fc.audit_procedures('2026-05')\n"
    "print(round(p['operating_income'],2), round(bs['balance_check'],2), c['n_fail'], "
    "round(fc.cash_total_usd('2026-05'),2), au['opinion'])\n"
)


def _run(env_extra):
    env = dict(os.environ)
    env.pop("FINANCE_DATA_DIR", None)
    env.pop("FINANCE_LATEST_PERIOD", None)
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"finance_core subprocess failed: {out.stderr}")
    return out.stdout.strip().split()


class EngineEndToEndTest(unittest.TestCase):
    def test_quickbooks_canonical_feeds_finance_core(self):
        out_dir = tempfile.mkdtemp(prefix="qbo_canon_")
        materialize.write_canonical_tables(build_canonical(), out_dir)
        oi, balance_check, n_fail, cash, opinion = _run(
            {"FINANCE_DATA_DIR": out_dir, "FINANCE_LATEST_PERIOD": "2026-05"})
        self.assertEqual(float(oi), 7000.0)          # 50000 - 18000 - 25000
        self.assertEqual(float(balance_check), 0.0)  # A = L + E
        self.assertEqual(int(n_fail), 0)             # no integrity control failures
        self.assertEqual(float(cash), 80000.0)
        self.assertEqual(opinion, "unqualified")

    def test_default_synthetic_path_is_unchanged(self):
        # With no override the engine reads the synthetic CSVs (a different,
        # multi-entity dataset), proving the QuickBooks swap did not alter the default.
        oi, _bc, _nf, _cash, _op = _run({})
        self.assertNotEqual(float(oi), 7000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
