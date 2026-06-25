"""test_contract.py - the canonical shape matches the engine's CSV contract, and
the synthetic source is untouched."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
from _fixture import build_canonical, REPO
from schema import CONTRACT_TABLES
import materialize
from connector import SyntheticConnector

SYNTH_DIR = os.path.join(REPO, "finance-mcp", "data")


def _header(path):
    with open(path, encoding="utf-8") as f:
        return f.readline().strip()


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_canonical_rows_carry_the_contract_columns(self):
        for name, cols in CONTRACT_TABLES.items():
            for row in self.c.get(name, []):
                missing = [c for c in cols if c not in row]
                self.assertEqual(missing, [], f"{name} row missing {missing}")

    def test_materialized_headers_equal_synthetic_headers(self):
        out = tempfile.mkdtemp(prefix="canon_")
        materialize.write_canonical_tables(self.c, out)
        for name in CONTRACT_TABLES:
            synth = os.path.join(SYNTH_DIR, name + ".csv")
            got = os.path.join(out, name + ".csv")
            self.assertTrue(os.path.exists(got), f"{name} not materialized")
            # byte-identical CSV header => the engine reads QBO data exactly like synthetic
            self.assertEqual(_header(got), _header(synth), f"{name} header drift")

    def test_synthetic_connector_reads_the_existing_csvs_unchanged(self):
        conn = SyntheticConnector()
        self.assertEqual(os.path.abspath(conn.data_dir), os.path.abspath(SYNTH_DIR))
        tables = conn.canonical_tables()
        self.assertGreater(len(tables["pnl_activity"]), 0)
        # contract columns present on the synthetic side too
        for c in CONTRACT_TABLES["pnl_activity"]:
            self.assertIn(c, tables["pnl_activity"][0])

    def test_synthetic_period_filter(self):
        conn = SyntheticConnector()
        rows = conn.fetch_pnl("2026-05")
        self.assertTrue(rows)
        self.assertTrue(all(r["period"] == "2026-05" for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
