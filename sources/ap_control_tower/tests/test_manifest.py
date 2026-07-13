"""test_manifest.py - the module-owned audit evidence: the manifest (sha256 +
provenance + boundary flags) and the trace CSV, both free of bank data / tax id,
and deterministic. Offline."""

import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util
import adapter
import contract

SENSITIVE_MARKERS = ("SYNTHETIC", "SYNTHXX", "SYNTH-TAX", "not a real institution")


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.base = _util.base_canonical()
        self.emap = _util.entity_map()
        self.res = adapter.ingest(self.base, _util.PERIOD, export_path=_util.FIXTURE,
                                  entity_map=self.emap, now_iso="1970-01-01T00:00:00+00:00")
        self.manifest = self.res["manifest"]

    def test_manifest_core_fields(self):
        m = self.manifest
        self.assertEqual(m["integration"], "ap_control_tower")
        self.assertEqual(m["period"], _util.PERIOD)
        self.assertEqual(m["imported_count"], 4)
        self.assertEqual(m["canonical_status"], "open")
        self.assertEqual(m["source_file"], "approved_payment_proposal.csv")
        self.assertEqual(m["totals_by_currency"], {"EUR": "17900.50", "USD": "37200.00"})

    def test_boundary_flags_are_explicit_and_false(self):
        for flag in ("bank_data_persisted", "tax_id_persisted",
                     "payment_executed", "posted_to_ledger"):
            self.assertIn(flag, self.manifest)
            self.assertFalse(self.manifest[flag])

    def test_validation_records_the_rules_enforced(self):
        v = self.manifest["validation"]
        self.assertTrue(v["passed"])
        for rule in ("status_is_approved_for_proposal", "entity_explicitly_mapped",
                     "human_approval_evidence_present_and_valid",
                     "currency_present_and_fx_covered", "amount_numeric_positive",
                     "no_duplicate_vs_canonical"):
            self.assertIn(rule, v["rules_enforced"])

    def test_source_sha256_matches_the_file(self):
        with open(_util.FIXTURE, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(self.manifest["source_sha256"], expected)

    def test_provenance_covers_every_row(self):
        prov = self.manifest["provenance"]
        self.assertEqual(len(prov), 4)
        trace_by_bill = {t["bill_id"]: t for t in self.res["trace_rows"]}
        for p in prov:
            self.assertEqual(set(p), {"source_row", "source_document",
                                      "bill_id", "entity_id"})
            t = trace_by_bill[p["bill_id"]]
            self.assertEqual(p["source_row"], t["source_row"])
            self.assertEqual(p["source_document"], t["source_document"])
            self.assertEqual(p["entity_id"], t["entity_id"])

    def test_manifest_has_no_sensitive_data(self):
        blob = json.dumps(self.manifest)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, blob)

    def test_trace_preserves_metadata_without_bank_detail(self):
        # row 0 keeps PO/project/method/approval/proposal-status; no bank/tax.
        t0 = next(t for t in self.res["trace_rows"]
                  if t["source_document"] == "AP-CT-INV-0001")
        self.assertEqual(t0["po_reference"], "PO-2026-0001")
        self.assertEqual(t0["project_reference"], "PRJ-EU-01")
        self.assertEqual(t0["payment_method"], "transferencia")
        self.assertEqual(t0["approved_by"], "Ana Approver (synthetic)")
        self.assertEqual(t0["proposal_status"], contract.APPROVED_STATUS)
        # non-PO row keeps an empty PO (not invented)
        t1 = next(t for t in self.res["trace_rows"]
                  if t["source_document"] == "AP-CT-INV-0002")
        self.assertEqual(t1["po_reference"], "")

    def test_write_outputs_trace_and_manifest(self):
        out = tempfile.mkdtemp(prefix="apct_ev_")
        paths = adapter.write_outputs(out, self.res)
        # trace.csv header is exactly TRACE_COLUMNS, with no banking columns
        with open(paths["trace"], newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            body = f.read()
        self.assertEqual(header, contract.TRACE_COLUMNS)
        for col in contract.BANK_COLUMNS + ("tax_id_proveedor",):
            self.assertNotIn(col, header)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, body)
        # manifest.json parses and round-trips
        with open(paths["manifest"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["imported_count"], 4)

    def test_manifest_is_deterministic(self):
        again = adapter.ingest(self.base, _util.PERIOD, export_path=_util.FIXTURE,
                               entity_map=self.emap, now_iso="1970-01-01T00:00:00+00:00")
        self.assertEqual(again["manifest"], self.manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
