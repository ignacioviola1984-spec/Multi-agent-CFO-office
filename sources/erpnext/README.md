# sources/erpnext/ - ERPNext (Frappe) as a second swappable source

ERPNext implements the **same `SourceConnector` interface** and feeds the **same
canonical layer** as QuickBooks. The CFO/O2C engine and `finance_core` never learn
an ERPNext object name: they read only canonical tables. This is not a parallel
integration; it is one more `SourceConnector` next to `QuickBooksConnector`.

```
ERPNext (Frappe) ─► adapter (read-only) ─► mapper ─► canonical tables ─► validate ─► snapshot
                                                              │
                              finance_core / MCP read ONLY ───┘  (FINANCE_DATA_DIR)
```

## Why ERPNext (the point)

The QuickBooks sandbox is single-entity / single-currency, so it could not
exercise multi-entity / multi-currency **consolidation**. ERPNext is
**multi-company** (each Company = a legal entity) and **multi-currency**, so this
source drives that consolidation through the real engine: two companies in USD and
GBP consolidate into one USD close, and `finance_core` foots the consolidated
balance sheet. (Honest boundary below.)

## Layers (mirrors `sources/quickbooks/`)

| File | Role |
|------|------|
| `auth.py` | Frappe **API key + secret** auth (header `Authorization: token <key>:<secret>`). No OAuth dance, no rotating refresh. Config from `.env`. |
| `adapter.py` | Read-only Frappe REST client. **GET only - no write method exists.** `/api/resource/<DocType>` (paged via `limit_start`/`limit_page_length`, explicit `fields`, JSON `filters`) and `/api/method/frappe.desk.query_report.run` for the financial statements. 429/5xx → exponential backoff. |
| `mapper.py` | Pure, deterministic **ERPNext → canonical** transform, multi-company / multi-currency. Routes every Account into one of the 12 canonical rollup codes; fills the engine tables **and** the optional Order-to-Cash tables. |
| `record_fixture.py` | Capture one real extraction to replace the test fixture. |

The connector itself is `ERPNextConnector` in `sources/canonical/connector.py` (the
shared interface lives there next to `SyntheticConnector` / `QuickBooksConnector`).

## DocType → canonical mapping

| ERPNext | Canonical table |
|---|---|
| Company | `entities` (entity_id = Company abbr, currency = default_currency) |
| Currency Exchange | `fx_rates` (as `units_per_usd`) |
| Account (tree) | routed into the 12-code `chart_of_accounts` |
| Profit and Loss Statement / Balance Sheet (reports) | `pnl_activity` / `balance_sheet` (per company) |
| Trial Balance | `trial_balance` (derived per company, pre-closing) |
| Sales Invoice (`is_return=0`) | `ar_invoices` |
| Sales Invoice (`is_return=1`) | `credit_notes` |
| Purchase Invoice | `ap_invoices` |
| Payment Entry | `payments` |
| GL Entry / Journal Entry | `journal_entries` |
| Customer / Supplier | `customers` / `vendors` |
| Opportunity (+ Lead) | `crm_opportunities` |
| Quotation | `quotations` |
| Sales Order | `sales_orders` |
| Dunning, Payment Request | `collections_reminders` |
| Bank Account, Bank Transaction | `cash_bank` |

The Order-to-Cash tables (`crm_opportunities, quotations, sales_orders,
credit_notes, collections_reminders, cash_bank`) **extend the shared canonical
schema** (`schema.O2C_TABLES`); they are filled by ERPNext and left empty by
QuickBooks/synthetic, so no engine or MCP code has to know which source produced
them.

> The exact DocType / report field names and report column shapes can vary by
> Frappe version. **Confirm against your live instance** and adjust the leaf-row
> parsing in `mapper.py` if a capture differs. `record_fixture.py` captures the
> real shapes.

## Setup (Frappe Cloud free trial or self-host)

1. Spin up a site: a **Frappe Cloud** free-trial site (`https://<site>.frappe.cloud`)
   or a self-hosted ERPNext. Load **demo data** (multiple Companies / currencies to
   exercise consolidation).
2. Create a dedicated **read-only user**: a Role with **Read** permission only on
   the relevant DocTypes (Company, Account, GL Entry, Sales/Purchase Invoice,
   Payment Entry, Sales Order, Quotation, Opportunity, Customer, Supplier, Bank
   Account, Currency Exchange) and on the financial-statement reports. No create /
   write / delete.
3. Generate **that user's** API key + secret (User → Settings → API Access →
   Generate Keys). The adapter is read-only in code; the read-only role makes it
   read-only on the server too (defense in depth).
4. Put the values in the repo-root `.env` (see [`../.env.example`](../.env.example)).
   **Never commit `.env`.**

## Run

```bash
# ERPNext: extract -> validate -> snapshot -> materialize canonical CSVs.
python sources/canonical/materialize.py --period 2026-05 --source erpnext
# -> prints the snapshot dir, the validation result, and:
#    export FINANCE_DATA_DIR=.../sources/canonical/_active

# The SAME engine then runs on ERPNext-sourced canonical data (consolidated):
FINANCE_DATA_DIR=$(python sources/canonical/materialize.py --print-data-dir --source erpnext) \
FINANCE_LATEST_PERIOD=2026-05 \
python -c "import sys; sys.path.insert(0,'orchestration'); import finance_core as fc; print(fc.pnl_usd('2026-05'))"

# Source-agnostic MCP surface (now includes get_sales_orders, get_quotations,
# get_credit_notes, get_collections, get_cash_bank):
SOURCE=erpnext python sources/mcp_server.py
```

## Tests

Part of `python sources/tests/run_tests.py` - fully **offline and deterministic**:
the ERPNext mapper, the shared validations (now per-entity / multi-currency), the
snapshot, the canonical-contract match, and an **engine end-to-end** test that
consolidates the two-company fixture through `finance_core`. CI never calls
ERPNext and needs no secret.

## Honest boundary

- **Multi-company / multi-currency consolidation IS exercised here** against real
  ERP data shapes - the gap the QuickBooks sandbox left open. The two-company
  fixture (USD + GBP) consolidates to one USD close and the engine foots it.
- **Still demo / sandbox data, not a production company.** This validates the
  pipeline, the mapping and the consolidation on real ERPNext shapes, not a real
  business's books.
- `budget` and `tax_obligations` are emitted empty for ERPNext in this version
  (out of scope; documented). The engine still loads.
- The committed fixture is **representative** (modeled on Frappe's documented
  response shapes); run `record_fixture.py` against your site for a genuine capture.
