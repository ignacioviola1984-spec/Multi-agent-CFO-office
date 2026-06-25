# sources/ — real data sources behind a swappable canonical layer

QuickBooks Online (sandbox) as a **real, swappable** data source feeding a
**canonical layer** the rest of the system already speaks. The CFO/O2C engine and
`finance_core` never learn a vendor's object names: today QuickBooks, tomorrow
NetSuite / SAP / Odoo / Zoho, with **zero engine changes**.

```
QuickBooks sandbox ─► adapter (read-only) ─► mapper ─► canonical tables ─► validate ─► snapshot
                                                                │
                                  finance_core / MCP read ONLY ─┘  (FINANCE_DATA_DIR)
```

## Layers

| File | Role |
|------|------|
| `quickbooks/oauth.py` | OAuth2 (auth-code) for the sandbox. Endpoints from Intuit's **discovery doc** (not hardcoded). Access token auto-refreshes (~60 min); the **refresh token rotates** (~24h) so the latest is always persisted with its new expiry. Token store is a JSON file **outside the repo**, gitignored. 401 → refresh → retry. |
| `quickbooks/adapter.py` | Read-only Accounting API client. `minorversion=75`. **GET/query only — no write method exists.** 429 → exponential backoff (honors `Retry-After`); 401 → refresh. Reports (P&L, BalanceSheet, TrialBalance, AgedReceivables, AgedPayables, GeneralLedger) and entity queries (Account, Invoice, Bill, Payment, BillPayment, Customer, Vendor, JournalEntry). |
| `quickbooks/mapper.py` | Pure, deterministic **QuickBooks → canonical** transform. Every QBO account routes into one of the 12 canonical rollup codes. |
| `canonical/schema.py` | The canonical schema. `CONTRACT_TABLES` = byte-identical columns to `finance-mcp/data/*.csv`, so the engine reads a QBO period exactly like the synthetic one. |
| `canonical/connector.py` | `SourceConnector` interface + `SyntheticConnector` (existing CSVs, untouched) + `QuickBooksConnector`. New vendors implement the same interface. |
| `canonical/validate.py` | Deterministic validations: balance sheet foots, trial balance balances, AR ties to control, no future-dated postings, currency present, counts > 0. Non-zero exit on failure. |
| `canonical/materialize.py` | Source selection (`SOURCE` env) + the QBO pipeline (extract → canonical → validate → snapshot → materialize CSVs). |
| `snapshots/writer.py` | **Immutable** snapshot: `raw/` (QBO JSON) + `canonical/` (CSV) + `manifest.json` (counts, period, realm, UTC timestamp, **sha256 of every file**, validation result). Append-only. |
| `mcp_server.py` | Source-agnostic, read-only MCP tools (`get_pnl`, `get_balance_sheet`, `get_trial_balance`, `get_ar_aging`, `get_ap_aging`, …). Swapping vendor does not change the surface. |

## Setup (QuickBooks sandbox)

1. Create an app at **developer.intuit.com** → keys for the **Development** (sandbox) environment.
2. Note your **sandbox company (realm) id** (the Intuit-provided US sample company).
3. Scope: `com.intuit.quickbooks.accounting` (this grants write too — the adapter is read-only **in code**).
4. Do the one-time consent to get a bootstrap **refresh token** (the auth-code flow; `oauth.authorize_url` / `oauth.exchange_code` help here).
5. Put the values in the repo-root `.env` (see [`.env.example`](.env.example)). **Never commit `.env` or the token store.**

## Run

```bash
# Synthetic (default): the engine reads finance-mcp/data, nothing to do.
SOURCE=synthetic

# QuickBooks: extract → validate → snapshot → materialize canonical CSVs.
python sources/canonical/materialize.py --period 2026-05 --source quickbooks
# -> prints the snapshot dir, the validation result, and:
#    export FINANCE_DATA_DIR=.../sources/canonical/_active

# Then the SAME engine runs on QuickBooks-sourced canonical data:
FINANCE_DATA_DIR=$(python sources/canonical/materialize.py --print-data-dir --source quickbooks) \
FINANCE_LATEST_PERIOD=2026-05 \
python -c "import sys; sys.path.insert(0,'orchestration'); import finance_core as fc; print(fc.pnl_usd('2026-05'))"

# Source-agnostic MCP surface:
python sources/mcp_server.py
```

`record_fixture.py` captures one real sandbox extraction to replace the test fixture.

## Canonical schema

`CONTRACT_TABLES` (read by `finance_core` / the MCP, columns identical to the
synthetic CSVs): `entities, fx_rates, chart_of_accounts, pnl_activity,
balance_sheet, budget, ar_invoices, ap_invoices, tax_obligations`.
Richer canonical tables carried in the snapshot and over MCP: `trial_balance,
payments, customers, vendors, journal_entries`. The 12-code roll-up chart lives
in `schema.CANONICAL_COA`.

## Tests

`python sources/tests/run_tests.py` — fully **offline and deterministic**: the
mapper, validations, snapshot, OAuth token store, the canonical-contract match,
and the engine end-to-end all run against a recorded **fixture**. CI never calls
QuickBooks and needs no secret.

## Honest boundary

- **Sandbox is not production.** This validates the pipeline and the mapping on
  real API shapes, not production data.
- **The sandbox sample company is single-entity / single-currency (US / USD).** So
  this exercises the **transactional layer (AR/AP/billing)** and **record-to-report
  (P&L, balance sheet, trial balance)** against real data, but **NOT multi-entity /
  multi-currency consolidation** — that path is only exercised by the synthetic
  Lumen model (6 entities, 6 currencies).
- **`budget` and `tax_obligations` are emitted empty** for QuickBooks: the sandbox
  has no clean object for them. The engine still loads; budget-variance and tax
  metrics are a synthetic-only feature, documented as such.
- The committed fixture is **representative** (modeled on Intuit's documented
  response shapes); run `record_fixture.py` against your realm for a genuine capture.
