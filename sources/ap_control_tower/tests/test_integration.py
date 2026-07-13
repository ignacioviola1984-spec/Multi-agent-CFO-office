"""test_integration.py - the imported proposals reach the EXISTING engine.

Materializes a base vs a merged canonical and runs finance_core over both in an
isolated subprocess (same pattern as sources/reconcile/ and the engine e2e test),
proving the real downstream consumers move: AP obligations, the Treasury 13-week
forecast, and the internal-controls / close / audit reconciliations - with no
engine change and nothing posted to the ledger. Offline, deterministic."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util
import adapter
import materialize
from connector import SyntheticConnector

_ORCH = os.path.join(_util.REPO, "orchestration")
PERIOD = _util.PERIOD
TOL = 0.5


def _run_engine(data_dir):
    script = (
        "import sys, os, json\n"
        f"sys.path.insert(0, {_ORCH!r})\n"
        "import finance_core as fc\n"
        "p = os.environ['AP_PERIOD']\n"
        "apm = fc.ap_metrics(p); sub = fc.subledger_totals_usd(p)\n"
        "cc = fc.control_checks(p); fcast = fc.cash_forecast_13w()\n"
        "close = fc.close_reconciliations(p); au = fc.audit_procedures(p)\n"
        "ap_rec = next((r for r in close['recs'] if r['item']=='Accounts payable'), {})\n"
        "ap_find = next((f for f in au['findings'] if f['proc'].startswith('Accounts payable')), {})\n"
        "print(json.dumps({\n"
        "  'ap_open_total': apm['open_total'], 'ap_subledger': sub['ap'],\n"
        "  'cc_n_fail': cc['n_fail'], 'cc_approval_exceptions': cc['approval_exceptions'],\n"
        "  'forecast_outflow_total': sum(w['outflow'] for w in fcast['rows']),\n"
        "  'close_ap_status': ap_rec.get('status'), 'close_all_reconciled': close['all_reconciled'],\n"
        "  'audit_ap_ok': ap_find.get('ok'), 'audit_opinion': au['opinion']}))\n"
    )
    env = dict(os.environ)
    env.pop("FINANCE_DATA_DIR", None)
    env.pop("FINANCE_LATEST_PERIOD", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["FINANCE_DATA_DIR"] = os.path.abspath(data_dir)
    env["FINANCE_LATEST_PERIOD"] = PERIOD
    env["AP_PERIOD"] = PERIOD
    out = subprocess.run([sys.executable, "-c", script], env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"finance_core subprocess failed:\n{out.stderr}")
    return json.loads(out.stdout.strip())


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _util.base_canonical()
        cls.base_ap_count = len(cls.base["ap_invoices"])
        cls.emap = _util.entity_map()
        cls.res = adapter.ingest(cls.base, PERIOD, export_path=_util.FIXTURE,
                                 entity_map=cls.emap, now_iso="1970-01-01T00:00:00+00:00")
        cls.merged = cls.res["merged_canonical"]
        fx = {r["currency"].upper(): float(r["units_per_usd"])
              for r in cls.base["fx_rates"] if r["period"] == PERIOD}
        fx["USD"] = 1.0
        cls.imported_usd = sum(float(r["amount_local"]) / fx[r["currency"]]
                               for r in cls.res["canonical_rows"])
        cls.base_dir = tempfile.mkdtemp(prefix="apct_base_")
        cls.merged_dir = tempfile.mkdtemp(prefix="apct_merged_")
        materialize.write_canonical_tables(cls.base, cls.base_dir)
        materialize.write_canonical_tables(cls.merged, cls.merged_dir)
        cls.before = _run_engine(cls.base_dir)
        cls.after = _run_engine(cls.merged_dir)

    def test_ap_obligations_increase_by_imported_total(self):
        delta = self.after["ap_open_total"] - self.before["ap_open_total"]
        self.assertAlmostEqual(delta, self.imported_usd, delta=TOL)
        self.assertGreater(delta, 0)

    def test_ap_subledger_increases_by_imported_total(self):
        delta = self.after["ap_subledger"] - self.before["ap_subledger"]
        self.assertAlmostEqual(delta, self.imported_usd, delta=TOL)

    def test_treasury_forecast_consumes_ap(self):
        delta = self.after["forecast_outflow_total"] - self.before["forecast_outflow_total"]
        self.assertAlmostEqual(delta, self.imported_usd, delta=TOL)

    def test_internal_controls_hard_gate_stays_green(self):
        self.assertEqual(self.before["cc_n_fail"], 0)
        self.assertEqual(self.after["cc_n_fail"], 0)

    def test_large_proposal_flagged_for_authorization(self):
        # the 28,000 USD proposal is >= the 25,000 authorization-review threshold
        self.assertEqual(self.after["cc_approval_exceptions"],
                         self.before["cc_approval_exceptions"] + 1)

    def test_close_and_audit_detect_the_unbooked_proposal(self):
        # A proposal is NOT a journal entry: the subledger grows but GL 2000 does
        # not, so the existing controls correctly flag it (not-yet-posted).
        self.assertEqual(self.after["close_ap_status"], "OPEN ITEM")
        self.assertFalse(self.after["close_all_reconciled"])
        self.assertFalse(self.after["audit_ap_ok"])
        self.assertIn(self.after["audit_opinion"], ("qualified", "adverse"))
        # and the untouched base still reconciles / is unqualified (we changed nothing there)
        self.assertTrue(self.before["close_all_reconciled"])
        self.assertEqual(self.before["audit_opinion"], "unqualified")

    def test_source_agnostic_ap_surface_lists_imported_bills(self):
        # fetch_ap() is exactly what the MCP get_ap_aging tool reads.
        ap = SyntheticConnector(data_dir=self.merged_dir).fetch_ap(PERIOD)
        by_id = {r["bill_id"]: r for r in ap}
        for r in self.res["canonical_rows"]:
            self.assertIn(r["bill_id"], by_id)
            self.assertEqual(by_id[r["bill_id"]]["status"], "open")

    def test_merge_does_not_mutate_the_base(self):
        # base is carried through unchanged; the imported rows live only in merged.
        self.assertEqual(len(self.base["ap_invoices"]), self.base_ap_count)
        self.assertEqual(len(self.merged["ap_invoices"]),
                         self.base_ap_count + self.res["imported_count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
