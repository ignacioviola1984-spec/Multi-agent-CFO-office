"""
contract.py - the input contract of AP Control Tower's approved-payment-proposal
export, and the target shapes on the canonical side.

The 17 columns below MIRROR AP Control Tower's `PAYMENT_EXPORT_COLUMNS`
(defined in that product at `ap_control_tower/ui/trial/payment_approval.py`).
They are DECLARED here, not imported: the CFO Office must not depend on the AP
Control Tower codebase. `tests/test_contract.py` pins this list so any drift is
caught deterministically; if AP Control Tower ever changes its export, this file
(and the adapter) change with an explicit, reviewed edit.

Nothing here interprets or maps bank details. `BANK_COLUMNS` names the three
banking fields the export carries; the adapter DROPS them and never writes them
to the canonical layer, the trace, the manifest, or any error message.
"""

# --------------------------------------------------------------------------
# 1) The input contract (AP Control Tower export) - 17 columns, exact order.
# --------------------------------------------------------------------------
PAYMENT_EXPORT_COLUMNS = [
    "beneficiario",
    "tax_id_proveedor",
    "factura_documento",
    "fecha_emision",
    "vencimiento",
    "moneda",
    "importe",
    "iban_cuenta",
    "bic_swift",
    "banco",
    "metodo_pago",
    "referencia_oc",
    "referencia_proyecto",
    "tipo_documental",
    "aprobado_por",
    "fecha_aprobacion",
    "estado",
]

# The ONLY exportable state that represents an approved proposal. Any other value
# is rejected fail-closed (a proposal that is not approved must never be ingested).
APPROVED_STATUS = "aprobada_para_propuesta"

# Banking fields present in the export. The adapter never persists or echoes
# these: an approved proposal is not a bank file and this integration executes
# no payment, so the destination account is intentionally out of scope here.
BANK_COLUMNS = ("iban_cuenta", "bic_swift", "banco")

# Sensitive fields never allowed to leak into the canonical layer, the trace, the
# manifest, or error text (bank details + the supplier tax id).
SENSITIVE_COLUMNS = BANK_COLUMNS + ("tax_id_proveedor",)


# --------------------------------------------------------------------------
# 2) The canonical target - the columns finance_core / the MCP already read.
#    Mirrors schema.CONTRACT_TABLES["ap_invoices"]; pinned by a test so the two
#    can never silently diverge.
# --------------------------------------------------------------------------
CANONICAL_AP_COLUMNS = [
    "bill_id",
    "entity_id",
    "vendor",
    "currency",
    "amount_local",
    "issue_date",
    "due_date",
    "status",
]

# The status every ingested proposal takes in the canonical layer. NEVER "paid":
# "approved for a payment proposal" does not mean paid, booked, or disbursed.
CANONICAL_STATUS_OPEN = "open"

# The mandatory direct field mapping (export column -> canonical column).
# `bill_id` is deliberately absent: the adapter derives a deterministic,
# collision-safe identifier from normalized beneficiary + factura_documento so
# two suppliers may legitimately use the same invoice number. `entity_id` is
# resolved ONLY from the explicit mapping provided to the adapter (never
# inferred). `status` is a constant ("open"), never taken from the file.
EXPORT_TO_CANONICAL = {
    "beneficiario": "vendor",
    "moneda": "currency",
    "importe": "amount_local",
    "fecha_emision": "issue_date",
    "vencimiento": "due_date",
}


# --------------------------------------------------------------------------
# 3) The traceability record - module-owned, kept clearly separate from the
#    shared canonical schema (no change to CONTRACT_TABLES / EXTRA_TABLES). It
#    preserves the richer proposal metadata (PO, project, method, human approval,
#    proposal status) plus provenance back to the export row, WITHOUT any bank
#    detail. Written next to the manifest as evidence for Internal Controls/Audit.
# --------------------------------------------------------------------------
TRACE_COLUMNS = [
    "source_row",          # 1-based row number in the export (provenance)
    "source_document",     # original factura_documento (human-readable provenance)
    "bill_id",             # deterministic canonical identity (supplier + document)
    "entity_id",           # resolved from the explicit mapping
    "vendor",
    "currency",
    "amount_local",
    "issue_date",
    "due_date",
    "po_reference",        # <- referencia_oc  (empty for non-PO invoices)
    "project_reference",   # <- referencia_proyecto
    "payment_method",      # <- metodo_pago
    "document_type",       # <- tipo_documental
    "approved_by",         # <- aprobado_por        (upstream human approval / maker-checker)
    "approved_at",         # <- fecha_aprobacion
    "proposal_status",     # <- estado              (== APPROVED_STATUS)
]

# The trace metadata mapping (export column -> trace column). Bank columns and
# tax id are intentionally excluded.
EXPORT_TO_TRACE = {
    "factura_documento": "source_document",
    "beneficiario": "vendor",
    "moneda": "currency",
    "importe": "amount_local",
    "fecha_emision": "issue_date",
    "vencimiento": "due_date",
    "referencia_oc": "po_reference",
    "referencia_proyecto": "project_reference",
    "metodo_pago": "payment_method",
    "tipo_documental": "document_type",
    "aprobado_por": "approved_by",
    "fecha_aprobacion": "approved_at",
    "estado": "proposal_status",
}
