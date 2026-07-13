"""test_adapter.py - fail-closed validation, the mandatory mapping to canonical
ap_invoices, privacy (no bank/tax), and the CSV/Excel transports. All offline."""

import copy
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util
import adapter
import contract
from adapter import APControlTowerError, EntityMap

SENSITIVE_MARKERS = ("SYNTHETIC", "SYNTHXX", "SYNTH-TAX", "not a real institution")


class Base(unittest.TestCase):
    def setUp(self):
        self.base = _util.base_canonical()
        self.emap = _util.entity_map()
        self.cols, self.rows = _util.read_fixture()
        self.known = {"USD"} | {r["currency"].upper() for r in self.base["fx_rates"]
                                if r["period"] == _util.PERIOD and r["currency"]}
        self.existing = {r["bill_id"] for r in self.base["ap_invoices"]}

    def problems(self, rows, cols=None, emap=None, **kw):
        return adapter.validate_export(cols or self.cols, rows, emap or self.emap,
                                       self.known, self.existing, **kw)

    def reasons(self, rows, **kw):
        return " | ".join(p.reason for p in self.problems(rows, **kw))


# --------------------------------------------------------------------------
# Happy path + mandatory mapping
# --------------------------------------------------------------------------
class HappyPathTest(Base):
    def test_clean_fixture_has_no_problems(self):
        self.assertEqual(self.problems(self.rows), [])

    def test_mapping_of_every_mandatory_field(self):
        canonical, _trace = adapter.map_export(self.rows, self.emap)
        self.assertEqual(len(canonical), 4)
        r0 = canonical[0]
        self.assertEqual(r0, {
            "bill_id": adapter.canonical_bill_id(
                "Synthetic Vendor Alpha SA", "AP-CT-INV-0001"),
            "entity_id": "DE",                    # <- explicit mapping
            "vendor": "Synthetic Vendor Alpha SA",  # <- beneficiario
            "currency": "EUR",                    # <- moneda
            "amount_local": "12500.00",           # <- importe
            "issue_date": "2026-04-15",           # <- fecha_emision
            "due_date": "2026-06-15",             # <- vencimiento
            "status": "open",                     # <- constant, never 'paid'
        })

    def test_every_record_is_open(self):
        canonical, _ = adapter.map_export(self.rows, self.emap)
        self.assertTrue(all(r["status"] == "open" for r in canonical))
        self.assertNotIn("paid", {r["status"] for r in canonical})

    def test_covers_two_currencies(self):
        canonical, _ = adapter.map_export(self.rows, self.emap)
        self.assertEqual({r["currency"] for r in canonical}, {"EUR", "USD"})

    def test_covers_two_explicit_entities(self):
        canonical, _ = adapter.map_export(self.rows, self.emap)
        self.assertEqual({r["entity_id"] for r in canonical}, {"DE", "US"})

    def test_totals_by_currency_and_entity(self):
        res = adapter.ingest(self.base, _util.PERIOD, columns=self.cols, rows=self.rows,
                             entity_map=self.emap, now_iso="1970-01-01T00:00:00+00:00")
        self.assertEqual(res["totals_by_currency"], {"EUR": "17900.50", "USD": "37200.00"})
        self.assertEqual(res["by_entity"]["DE"]["by_currency"], {"EUR": "17900.50"})
        self.assertEqual(res["by_entity"]["US"]["by_currency"], {"USD": "37200.00"})
        self.assertEqual(res["imported_count"], 4)

    def test_canonical_identity_uses_vendor_and_document_not_tax_id(self):
        original = copy.deepcopy(self.rows[:1])
        changed_tax = copy.deepcopy(original)
        changed_tax[0]["tax_id_proveedor"] = "A-DIFFERENT-TAX-ID"
        changed_vendor = copy.deepcopy(original)
        changed_vendor[0]["beneficiario"] = "Another Synthetic Supplier"
        original_id = adapter.map_export(original, self.emap)[0][0]["bill_id"]
        self.assertEqual(
            adapter.map_export(changed_tax, self.emap)[0][0]["bill_id"], original_id)
        self.assertNotEqual(
            adapter.map_export(changed_vendor, self.emap)[0][0]["bill_id"], original_id)


# --------------------------------------------------------------------------
# Entity mapping (explicit only; never inferred)
# --------------------------------------------------------------------------
class EntityMappingTest(Base):
    def test_unmapped_row_is_rejected(self):
        emap = EntityMap(by_document={"AP-CT-INV-0001": "DE"}, default=None)
        rs = self.reasons(self.rows, emap=emap)
        self.assertIn("no explicit entity mapping", rs)

    def test_default_entity_applies_when_no_document_match(self):
        emap = EntityMap(default="US")
        self.assertEqual(emap.resolve("anything"), "US")
        self.assertEqual(self.problems(self.rows, emap=emap), [])

    def test_by_document_takes_precedence_over_default(self):
        emap = EntityMap(by_document={"AP-CT-INV-0002": "BR"}, default="US")
        self.assertEqual(emap.resolve("AP-CT-INV-0002"), "BR")
        self.assertEqual(emap.resolve("AP-CT-INV-0009"), "US")

    def test_by_identity_disambiguates_same_document_number(self):
        identity = adapter.canonical_bill_id("Supplier Two", "INV-001")
        emap = EntityMap(by_document={"INV-001": "US"},
                         by_identity={identity: "DE"})
        self.assertEqual(emap.resolve("INV-001", "Supplier One"), "US")
        self.assertEqual(emap.resolve("INV-001", "Supplier Two"), "DE")

    def test_no_mapping_at_all_resolves_to_none(self):
        self.assertIsNone(EntityMap().resolve("x"))

    def test_ingest_requires_an_entity_map(self):
        with self.assertRaises(APControlTowerError):
            adapter.ingest(self.base, _util.PERIOD, columns=self.cols, rows=self.rows,
                           entity_map=None)


# --------------------------------------------------------------------------
# Fail-closed rules
# --------------------------------------------------------------------------
class FailClosedTest(Base):
    def _mutate(self, idx, **changes):
        rows = copy.deepcopy(self.rows)
        rows[idx].update(changes)
        return rows

    def test_reject_status_not_approved(self):
        self.assertIn("estado is not", self.reasons(self._mutate(0, estado="rechazada")))
        self.assertIn("estado is not", self.reasons(self._mutate(0, estado="")))

    def test_reject_missing_human_approval_evidence(self):
        self.assertIn("aprobado_por is empty",
                      self.reasons(self._mutate(0, aprobado_por="")))
        self.assertIn("fecha_aprobacion is not a timezone-aware ISO datetime",
                      self.reasons(self._mutate(0, fecha_aprobacion="")))

    def test_reject_invalid_or_ambiguous_approval_timestamp(self):
        for bad in ("not-a-date", "2026-05-02T10:00:00"):
            with self.subTest(fecha_aprobacion=bad):
                self.assertIn("timezone-aware ISO datetime",
                              self.reasons(self._mutate(0, fecha_aprobacion=bad)))

    def test_accept_zulu_approval_timestamp(self):
        self.assertEqual(self.problems(
            self._mutate(0, fecha_aprobacion="2026-05-02T10:00:00Z")), [])

    def test_reject_empty_bill_id(self):
        self.assertIn("factura_documento (bill_id) is empty",
                      self.reasons(self._mutate(0, factura_documento="")))

    def test_reject_empty_vendor(self):
        self.assertIn("beneficiario (vendor) is empty",
                      self.reasons(self._mutate(0, beneficiario="")))

    def test_reject_empty_currency(self):
        self.assertIn("moneda is empty", self.reasons(self._mutate(0, moneda="")))

    def test_reject_currency_not_fx_covered(self):
        self.assertIn("not covered by the available FX rates",
                      self.reasons(self._mutate(0, moneda="JPY")))

    def test_reject_amount_invalid(self):
        for bad, marker in [("", "empty or not numeric"), ("abc", "empty or not numeric"),
                            ("0", "must be > 0"), ("-5", "must be > 0"),
                            ("1,000.00", "empty or not numeric")]:
            with self.subTest(importe=bad):
                self.assertIn(marker, self.reasons(self._mutate(0, importe=bad)))

    def test_reject_invalid_dates(self):
        self.assertIn("fecha_emision is not a valid ISO date",
                      self.reasons(self._mutate(0, fecha_emision="2026-13-01")))
        self.assertIn("vencimiento is not a valid ISO date",
                      self.reasons(self._mutate(0, vencimiento="not-a-date")))

    def test_reject_due_before_issue(self):
        rows = self._mutate(0, fecha_emision="2026-05-10", vencimiento="2026-05-01")
        self.assertIn("vencimiento is before fecha_emision", self.reasons(rows))

    def test_due_before_issue_allowed_when_justified(self):
        rows = self._mutate(0, fecha_emision="2026-05-10", vencimiento="2026-05-01")
        self.assertEqual(self.problems(rows, allow_due_before_issue=True), [])
        self.assertEqual(self.problems(rows, justified_docs={"AP-CT-INV-0001"}), [])

    def test_reject_duplicate_within_file(self):
        rows = copy.deepcopy(self.rows)
        rows[1]["factura_documento"] = rows[0]["factura_documento"]
        rows[1]["beneficiario"] = rows[0]["beneficiario"]
        self.assertIn("duplicate supplier + factura_documento", self.reasons(rows))

    def test_same_document_number_from_different_suppliers_is_valid(self):
        rows = copy.deepcopy(self.rows[:2])
        rows[1]["factura_documento"] = rows[0]["factura_documento"]
        self.assertEqual(self.problems(rows), [])
        canonical, _trace = adapter.map_export(rows, self.emap)
        self.assertEqual(len({r["bill_id"] for r in canonical}), 2)

    def test_reject_duplicate_against_canonical(self):
        rows = copy.deepcopy(self.rows[:1])
        replay_id = adapter.canonical_bill_id(
            rows[0]["beneficiario"], rows[0]["factura_documento"])
        problems = adapter.validate_export(
            self.cols, rows, self.emap, self.known, {replay_id})
        rs = " | ".join(p.reason for p in problems)
        self.assertIn("already exists in canonical ap_invoices", rs)

    def test_reject_missing_contract_column(self):
        cols = [c for c in self.cols if c != "importe"]
        rows = [{k: v for k, v in r.items() if k != "importe"} for r in self.rows]
        rs = " | ".join(p.reason for p in self.problems(rows, cols=cols))
        self.assertIn("do not match the 17-column contract", rs)
        self.assertIn("missing", rs)

    def test_reject_unexpected_extra_column(self):
        cols = self.cols + ["extra_col"]
        rows = copy.deepcopy(self.rows)
        for r in rows:
            r["extra_col"] = "x"
        rs = " | ".join(p.reason for p in self.problems(rows, cols=cols))
        self.assertIn("do not match the 17-column contract", rs)

    def test_ingest_raises_on_any_violation(self):
        bad = self._mutate(0, importe="-1")
        with self.assertRaises(APControlTowerError) as cm:
            adapter.ingest(self.base, _util.PERIOD, columns=self.cols, rows=bad,
                           entity_map=self.emap)
        self.assertTrue(cm.exception.problems)


# --------------------------------------------------------------------------
# Privacy: no bank detail or tax id ever persisted or echoed
# --------------------------------------------------------------------------
class PrivacyTest(Base):
    def test_no_bank_columns_in_canonical(self):
        canonical, _ = adapter.map_export(self.rows, self.emap)
        for r in canonical:
            for col in contract.BANK_COLUMNS + ("tax_id_proveedor",):
                self.assertNotIn(col, r)

    def test_no_bank_columns_in_trace(self):
        _c, trace = adapter.map_export(self.rows, self.emap)
        for t in trace:
            for col in contract.BANK_COLUMNS + ("tax_id_proveedor",):
                self.assertNotIn(col, t)

    def test_no_bank_values_leak_into_mapped_output(self):
        import json
        canonical, trace = adapter.map_export(self.rows, self.emap)
        blob = json.dumps(canonical) + json.dumps(trace)
        for marker in SENSITIVE_MARKERS:
            self.assertNotIn(marker, blob)

    def test_error_message_contains_no_bank_or_tax(self):
        bad = copy.deepcopy(self.rows)
        bad[0]["importe"] = "-1"          # force a rejection that references the row
        try:
            adapter.ingest(self.base, _util.PERIOD, columns=self.cols, rows=bad,
                           entity_map=self.emap)
            self.fail("expected rejection")
        except APControlTowerError as exc:
            for marker in SENSITIVE_MARKERS:
                self.assertNotIn(marker, str(exc))


# --------------------------------------------------------------------------
# No payment execution (structural)
# --------------------------------------------------------------------------
class NoPaymentTest(Base):
    def test_adapter_exposes_no_payment_execution(self):
        for name in ("execute", "pay", "execute_payment", "release", "disburse", "post"):
            self.assertFalse(hasattr(adapter, name),
                             f"adapter must not expose a '{name}' capability")

    def test_ingest_never_produces_paid_status(self):
        res = adapter.ingest(self.base, _util.PERIOD, columns=self.cols, rows=self.rows,
                             entity_map=self.emap, now_iso="1970-01-01T00:00:00+00:00")
        self.assertEqual({r["status"] for r in res["canonical_rows"]}, {"open"})
        self.assertFalse(res["manifest"]["payment_executed"])
        self.assertFalse(res["manifest"]["posted_to_ledger"])


# --------------------------------------------------------------------------
# Transports: CSV (primary) and Excel (optional, openpyxl)
# --------------------------------------------------------------------------
class TransportTest(Base):
    def test_csv_reads_the_17_columns(self):
        cols, rows = adapter.read_export(_util.FIXTURE)
        self.assertEqual(cols, contract.PAYMENT_EXPORT_COLUMNS)
        self.assertEqual(len(rows), 4)

    def test_excel_transport_matches_csv(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed (CSV is the tested transport)")
        cols, rows = _util.read_fixture()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(cols)
        for r in rows:
            ws.append([str(r[c]) for c in cols])   # write text so numbers round-trip exactly
        path = os.path.join(tempfile.mkdtemp(prefix="apct_xlsx_"), "proposal.xlsx")
        wb.save(path)
        x_cols, x_rows = adapter.read_export(path)
        self.assertEqual(x_cols, cols)
        # the mapping is identical whether the file arrived as CSV or Excel
        self.assertEqual(adapter.map_export(x_rows, self.emap),
                         adapter.map_export(rows, self.emap))


class PackageImportTest(unittest.TestCase):
    def test_adapter_imports_as_a_normal_package(self):
        code = ("from sources.ap_control_tower import adapter; "
                "print(adapter.canonical_bill_id('Vendor', 'INV-1'))")
        out = subprocess.run([sys.executable, "-c", code], cwd=_util.REPO,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.strip().startswith("APCT-"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
