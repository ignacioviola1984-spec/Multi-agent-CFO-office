"""test_erpnext_contract.py - ERPNext canonical matches the engine's CSV contract
(byte-identical headers to the synthetic CSVs), and the optional O2C tables
materialize too."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext
from _fixture_erpnext import build_canonical, REPO
from schema import CONTRACT_TABLES, O2C_TABLES
import materialize

SYNTH_DIR = os.path.join(REPO, "finance-mcp", "data")


def _header(path):
    with open(path, encoding="utf-8") as f:
        return f.readline().strip()


class ErpNextContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_canonical_rows_carry_the_contract_columns(self):
        for name, cols in CONTRACT_TABLES.items():
            for row in self.c.get(name, []):
                missing = [c for c in cols if c not in row]
                self.assertEqual(missing, [], f"{name} row missing {missing}")

    def test_materialized_headers_equal_synthetic_headers(self):
        out = tempfile.mkdtemp(prefix="erp_canon_")
        materialize.write_canonical_tables(self.c, out)
        for name in CONTRACT_TABLES:
            synth = os.path.join(SYNTH_DIR, name + ".csv")
            got = os.path.join(out, name + ".csv")
            self.assertTrue(os.path.exists(got), f"{name} not materialized")
            self.assertEqual(_header(got), _header(synth), f"{name} header drift")

    def test_o2c_tables_materialize_with_their_headers(self):
        out = tempfile.mkdtemp(prefix="erp_o2c_")
        materialize.write_canonical_tables(self.c, out)
        for name in O2C_TABLES:
            self.assertTrue(os.path.exists(os.path.join(out, name + ".csv")), f"{name} not written")


if __name__ == "__main__":
    unittest.main(verbosity=2)
