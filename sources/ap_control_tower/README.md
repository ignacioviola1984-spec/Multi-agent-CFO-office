# AP Control Tower → canonical `ap_invoices` (reference adapter)

A **bounded, read-only adapter** that ingests **AP Control Tower**'s *approved
payment proposal* export into the canonical finance layer the CFO Office already
reads. Each approved proposal becomes one canonical **open** `ap_invoices`
record, so the **existing** engine and agents consume it **with zero engine
changes**.

> **AP Control Tower is a separate, independent product** (its own repository,
> `ignacioviola1984-spec/ap-control-tower`). This adapter lives entirely inside
> the CFO Office and only *reads a file* that AP Control Tower exports. Nothing
> here depends on, imports from, or writes back to AP Control Tower.

## Purpose

AP Control Tower turns supplier invoices into a **human-approved, controlled
payment proposal** and exports it (CSV/Excel). It **does not** post accounting,
generate a bank file, or release money. The CFO Office needs those approved
payables visible as **accounts-payable obligations** for AP operations, Treasury
cash forecasting, internal controls and audit — **without** treating a proposal
as a booked or paid item. This adapter is exactly that seam, and nothing more.

Direction is **one-way only**:

```
AP Control Tower export  ->  adapter  ->  canonical ap_invoices  ->  existing CFO Office consumers
```

## Why an adapter, not a `SourceConnector`

The canonical layer's `SourceConnector` (see [`../README.md`](../README.md))
requires a **full** financial source — P&L, balance sheet, trial balance — and
`sources/canonical/validate.py` validates a complete, footing ledger. AP Control
Tower delivers a **partial, approved accounts-payable feed**, not a ledger. So
this is a **pure mapper** (the same shape as
[`../erpnext/mapper.py`](../erpnext/mapper.py)'s `map_ap_invoices`) plus a small
merge helper that combines the mapped rows with an existing canonical set. No
second finance engine, no duplicated calculations, and **no change** to the
shared `schema.py` (`CONTRACT_TABLES` / `EXTRA_TABLES` are untouched).

## Input contract (the export)

The export has the **17 columns** AP Control Tower writes
(`ap_control_tower/ui/trial/payment_approval.py::PAYMENT_EXPORT_COLUMNS`). They
are **declared** in [`contract.py`](contract.py), not imported (the CFO Office
must not depend on the AP Control Tower codebase); `tests/test_contract.py` pins
the list so any drift is caught. The only exportable state that represents an
approved proposal is **`aprobada_para_propuesta`**. That state is not trusted by
itself: `aprobado_por` must be present and `fecha_aprobacion` must be a valid,
timezone-aware ISO timestamp, or the whole file is rejected.

## Mapping (mandatory)

Each approved row maps to a canonical `ap_invoices` record:

| canonical column | source |
|---|---|
| `bill_id` | deterministic `APCT-…` identity from normalized `beneficiario` + `factura_documento` |
| `entity_id` | **explicit mapping provided to the adapter** (never inferred) |
| `vendor` | `beneficiario` |
| `currency` | `moneda` |
| `amount_local` | `importe` |
| `issue_date` | `fecha_emision` |
| `due_date` | `vencimiento` |
| `status` | constant **`open`** — never `paid` |

Invoice numbers are supplier-scoped, not globally unique. The deterministic
canonical id therefore prevents both false duplicates (same number, different
supplier) and replay (same normalized supplier + number). It uses no tax id.
The original human-readable `factura_documento` remains in trace provenance as
`source_document`.

The export carries **no entity column**, so `entity_id` comes only from an
explicit `EntityMap`: `by_identity` can disambiguate the deterministic identity,
`by_document` supports a unique source document, and/or a single `default` may
be supplied. A row that resolves to no entity is rejected. The richer proposal
metadata (PO, project, payment method, human approver, proposal status) is
preserved in a **separate, module-owned traceability record** (`trace.csv` + `manifest.json`),
kept clearly apart from the shared canonical schema. **Bank details
(`iban_cuenta`, `bic_swift`, `banco`) and the supplier tax id are dropped** and
never persisted, echoed, or logged.

## Validations (fail-closed)

[`adapter.py`](adapter.py) rejects the whole file, with a **row + reason** (never
echoing bank data or tax ids), on any of:

1. columns that don't match the 17-column contract;
2. `estado` other than `aprobada_para_propuesta`;
3. missing/invalid human approval evidence (`aprobado_por` plus timezone-aware `fecha_aprobacion`);
4. empty `factura_documento`; 5. empty `beneficiario`;
6. an `entity_id` with no explicit mapping;
7. an empty currency, or one **not covered by the available FX rates**;
8. an amount that is empty, non-numeric, zero, or negative;
9. an invalid `fecha_emision` / `vencimiento`;
10. `vencimiento` before `fecha_emision` (unless an explicit, tested justification);
11. a duplicate supplier + document identity within the file;
12. a duplicate identity against the existing canonical `ap_invoices` (replay protection).

## End-to-end flow

```
read_export ──► validate_export (12 fail-closed rules) ──► map_export ──► merge_into_canonical
                                                       └─► build_manifest  (sha256 + provenance)
```

The mapped rows are appended to an existing canonical set (the synthetic
`SyntheticConnector` here) and materialized to a canonical dir the **existing
`finance_core`** reads via `FINANCE_DATA_DIR` — the same isolation pattern as
[`../reconcile/`](../reconcile/README.md). Downstream, with **no engine change**:

- **Administration / AP** — `finance_core.ap_metrics()` (used by
  `cfo-office/ap_agent.py`): AP obligations (`open_total`, overdue, upcoming, DPO)
  rise by exactly the imported open total.
- **Treasury** — `finance_core.cash_forecast_13w()` (used by
  `cfo-office/treasury_agent.py`): the imported payables become 13-week cash
  **outflows** by `due_date`.
- **Internal Controls** — `finance_core.control_checks()`: the hard gate stays
  green (`n_fail` unchanged), and any proposal at/above the authorization
  threshold is **flagged for authorization review** (control C5 detects it).
- **Audit / Close** — because *a proposal is not a journal entry*, the close's
  **AP subledger → GL** reconciliation and the **independent audit** correctly
  flag the imported obligations as **not-yet-posted** (an open reconciling item
  equal to the imported total). The adapter never touches the ledger, and the
  existing controls remain effective.

## Run the demo / tests (offline, no API key)

```bash
# Reproducible end-to-end evidence (exits 0 on success, non-zero on any violation):
python sources/ap_control_tower/demo.py

# Deterministic test suite:
python sources/ap_control_tower/tests/run_tests.py
```

The demo prints the fixture read, the imported count, totals by currency, the
mapping by entity, the resulting canonical records, the AP-obligations and
Treasury deltas, the controls/close/audit behavior, the fail-closed rejection of
an invalid row, and the duplicate/replay rejection — **all computed, never
hand-written**. It writes each run to a unique system-temporary directory (or
under `AP_CT_DEMO_ROOT` when set), avoiding destructive cleanup and remaining
repeatable from Windows/OneDrive checkouts.

## Privacy

The fixture ([`fixtures/approved_payment_proposal.csv`](fixtures/approved_payment_proposal.csv))
is **100% synthetic** and clearly labelled (`Synthetic Vendor …`, `SYNTH-TAX-…`,
`XX00SYNTHETIC…`, "not a real institution"), with the **same 17 columns** the
real product exports. No real invoices, results, supplier names, tax ids, IBANs,
BICs, emails, or client data are copied or published. Bank details and tax ids
are never carried into the canonical layer, the trace, the manifest, or any
error message (enforced by tests).

## Honest limits

- **This is a reference integration, not production.** It validates the contract,
  the mapping, and the downstream wiring on the real export shape — not a
  production company's data.
- **Transport is CSV/Excel today.** There is **no real-time API**, **no
  cross-application authentication**, **no bidirectional sync**, and **no shared
  database** between the two products.
- **No payment is executed and nothing is posted to the ledger.** An "approved
  payment proposal" is a controlled obligation, not a paid or booked item; it
  surfaces as open AP and as auditable provenance only. The governed payment
  path (`payments/`) is a **separate** seam and is intentionally **not** wired to
  this feed.
- **The source sha256 proves which bytes were processed; it does not authenticate
  who created the export.** Source signing/authentication remains out of scope for
  this offline reference seam.
- **AP Control Tower stays an independent product and repository.** This adapter
  only reads its exported file and never modifies, branches, deploys, or depends
  on it.
