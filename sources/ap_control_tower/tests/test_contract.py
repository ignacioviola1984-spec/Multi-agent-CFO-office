"""test_contract.py - the input contract is exactly AP Control Tower's 17 columns,
the canonical target matches the shared schema, and no sensitive field is carried."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: F401  (sets up sys.path)
import contract


# The 17 columns exactly as AP Control Tower exports them
# (ap_control_tower/ui/trial/payment_approval.py::PAYMENT_EXPORT_COLUMNS).
EXPECTED_17 = [
    "beneficiario", "tax_id_proveedor", "factura_documento", "fecha_emision",
    "vencimiento", "moneda", "importe", "iban_cuenta", "bic_swift", "banco",
    "metodo_pago", "referencia_oc", "referencia_proyecto", "tipo_documental",
    "aprobado_por", "fecha_aprobacion", "estado",
]


class ContractTest(unittest.TestCase):
    def test_exactly_the_17_contract_columns_in_order(self):
        self.assertEqual(contract.PAYMENT_EXPORT_COLUMNS, EXPECTED_17)
        self.assertEqual(len(contract.PAYMENT_EXPORT_COLUMNS), 17)

    def test_fixture_header_matches_the_contract(self):
        cols = _util.columns()
        self.assertEqual(cols, EXPECTED_17,
                         "the synthetic fixture must mirror the real export header exactly")

    def test_only_approved_status_is_ingestible(self):
        self.assertEqual(contract.APPROVED_STATUS, "aprobada_para_propuesta")

    def test_canonical_target_matches_shared_schema(self):
        # sources/canonical/schema.py must stay the single source of truth.
        import schema
        self.assertEqual(contract.CANONICAL_AP_COLUMNS,
                         schema.CONTRACT_TABLES["ap_invoices"])

    def test_canonical_status_is_open_never_paid(self):
        self.assertEqual(contract.CANONICAL_STATUS_OPEN, "open")

    def test_trace_carries_no_sensitive_field(self):
        # No bank detail and no tax id may appear in the traceability record.
        for col in contract.BANK_COLUMNS + ("tax_id_proveedor",):
            self.assertNotIn(col, contract.TRACE_COLUMNS)
        # The trace DOES preserve the richer proposal metadata + provenance.
        for col in ("source_row", "source_document", "po_reference", "project_reference",
                    "payment_method", "approved_by", "approved_at", "proposal_status"):
            self.assertIn(col, contract.TRACE_COLUMNS)

    def test_bank_columns_are_the_three_banking_fields(self):
        self.assertEqual(set(contract.BANK_COLUMNS), {"iban_cuenta", "bic_swift", "banco"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
