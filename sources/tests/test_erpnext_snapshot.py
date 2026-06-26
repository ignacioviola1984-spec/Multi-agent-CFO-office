"""test_erpnext_snapshot.py - the immutable snapshot carries the ERPNext source
identity (source, site_url, companies) and the O2C tables, with hashes."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext
from _fixture_erpnext import build_canonical, load_raw, PERIOD
import validate as V
import writer as snapshot_writer

TS = "2026-05-31T12:00:00+00:00"


class ErpNextSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_raw()
        cls.canonical = build_canonical()
        cls.vr = V.validate_canonical(cls.canonical, PERIOD)

    def test_manifest_carries_source_and_site(self):
        base = tempfile.mkdtemp(prefix="erp_snap_")
        snap_dir, manifest = snapshot_writer.write_snapshot(
            base, "demo.frappe.cloud", PERIOD, self.raw, self.canonical, self.vr, TS,
            source="erpnext", extra={"site_url": "https://demo.frappe.cloud",
                                     "companies": ["Lumen US Inc.", "Lumen UK Ltd."]})
        self.assertEqual(manifest["source"], "erpnext")
        self.assertEqual(manifest["site_url"], "https://demo.frappe.cloud")
        self.assertIn("Lumen UK Ltd.", manifest["companies"])
        self.assertEqual(manifest["validation_result"]["pass"], True)
        # snapshot path uses the site identity
        self.assertIn("demo.frappe.cloud", snap_dir)

    def test_o2c_tables_and_hashes_are_in_the_snapshot(self):
        base = tempfile.mkdtemp(prefix="erp_snap2_")
        snap_dir, manifest = snapshot_writer.write_snapshot(
            base, "site", PERIOD, self.raw, self.canonical, self.vr, TS, source="erpnext")
        self.assertGreater(manifest["record_counts"]["sales_orders"], 0)
        self.assertTrue(os.path.exists(os.path.join(snap_dir, "canonical", "sales_orders.csv")))
        self.assertEqual(len(manifest["hashes"]["canonical"]["sales_orders.csv"]), 64)
        # raw responses captured too
        self.assertTrue(os.path.exists(os.path.join(snap_dir, "raw", "sales_orders.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
