"""test_snapshot.py - immutable snapshots: structure, manifest, hashes, append-only."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
from _fixture import build_canonical, load_raw, PERIOD
import validate as V
import writer as snapshot_writer


class SnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_raw()
        cls.canonical = build_canonical()
        cls.vr = V.validate_canonical(cls.canonical, PERIOD)

    def _write(self, base, ts):
        return snapshot_writer.write_snapshot(base, "9999", PERIOD, self.raw, self.canonical, self.vr, ts)

    def test_structure_and_manifest(self):
        base = tempfile.mkdtemp(prefix="snap_")
        snap_dir, manifest = self._write(base, "2026-06-25T14:00:00+00:00")
        self.assertTrue(os.path.isdir(os.path.join(snap_dir, "raw")))
        self.assertTrue(os.path.isdir(os.path.join(snap_dir, "canonical")))
        self.assertTrue(os.path.exists(os.path.join(snap_dir, "manifest.json")))
        # raw + canonical files were written
        self.assertTrue(os.path.exists(os.path.join(snap_dir, "raw", "profit_and_loss.json")))
        self.assertTrue(os.path.exists(os.path.join(snap_dir, "canonical", "pnl_activity.csv")))
        # manifest content
        self.assertEqual(manifest["realm_id"], "9999")
        self.assertEqual(manifest["period"], PERIOD)
        self.assertEqual(manifest["extract_timestamp"], "2026-06-25T14:00:00+00:00")
        self.assertEqual(manifest["validation_result"]["pass"], True)
        self.assertEqual(manifest["record_counts"]["pnl_activity"], 5)
        # every raw + canonical file has a sha256
        self.assertEqual(len(manifest["hashes"]["raw"]["profit_and_loss.json"]), 64)
        self.assertEqual(len(manifest["hashes"]["canonical"]["balance_sheet.csv"]), 64)

    def test_hash_is_deterministic(self):
        base = tempfile.mkdtemp(prefix="snap_")
        _, m1 = self._write(base, "2026-06-25T14:00:00+00:00")
        _, m2 = self._write(base + "_b", "2026-06-25T15:00:00+00:00")
        # same canonical content -> identical canonical hashes regardless of when
        self.assertEqual(m1["hashes"]["canonical"]["pnl_activity.csv"],
                         m2["hashes"]["canonical"]["pnl_activity.csv"])

    def test_append_only_distinct_timestamps(self):
        base = tempfile.mkdtemp(prefix="snap_")
        d1, _ = self._write(base, "2026-06-25T14:00:00+00:00")
        d2, _ = self._write(base, "2026-06-25T15:30:00+00:00")
        self.assertNotEqual(d1, d2)            # different extract -> different dir
        self.assertTrue(os.path.exists(os.path.join(d1, "manifest.json")))
        self.assertTrue(os.path.exists(os.path.join(d2, "manifest.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
