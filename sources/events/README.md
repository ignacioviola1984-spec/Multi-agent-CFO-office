# sources/events/ - event-driven (webhook) ingestion

Batch pull (a QuickBooks / ERPNext snapshot) is not how wallet and payment
infrastructure works - deposits and payout confirmations arrive as **webhooks**.
This module adds a push path that lands in the **same canonical layer** as the
batch connectors, so `finance_core` never learns whether a period arrived by pull
or by push.

```
provider webhook ─► receiver (HMAC verify) ─► event store (append-only, idempotent)
                          │ reject → audit            │
                          ▼                           ▼  replay (any order)
                    governance audit            mapper ─► canonical tables ─► CSV + sha256 manifest
                                                              (cash_bank, payments)
```

## Pieces

| File | Role |
|---|---|
| `signing.py` | Per-source HMAC-SHA256 verification. Secret via the `SecretsProvider` (`WEBHOOK_HMAC_<SOURCE>` + `_NEXT`). Accepts either secret → **dual-secret rotation window** ([`config/ROTATION.md`](../../config/ROTATION.md)). Constant-time. Fails closed. |
| `store.py` | Append-only, **idempotent** event store keyed on `(source, event_id)`. Records source, event id, received-at, sha256 payload hash. Duplicate ids are recorded but **not re-processed**. Redaction-aware. |
| `mappers.py` | Per-source `event → canonical` mappers. `WalletProviderMapper` (deposit.created / withdrawal.confirmed → `cash_bank` balance + `payments`). **Order-independent** (folds events in canonical order). |
| `receiver.py` | `receive(...)` - the pure, testable core (verify → reject+audit → store idempotently). `make_server(...)` - a minimal **stdlib** HTTP layer so it runs with no new dependency. |
| `connector.py` | `EventSourceConnector(SourceConnector)` - the push side of the same connector contract; emits canonical tables identical in shape to the pull sources. |
| `replay.py` | Rebuild canonical state from the event store → canonical CSVs + a manifest with a **sha256** of every file. |

## The receiver (and the FastAPI/Flask note)

The security-critical logic lives in `receive(source, raw_body, signature_header,
store)`, which is framework-free and fully unit-tested offline. `make_server()`
puts Python's standard-library `http.server` in front of it so the receiver
**actually runs with zero new dependencies** - consistent with the repo's
offline, no-API-key posture:

```bash
python sources/events/receiver.py --port 8000
# POST /webhooks/wallet  with header  X-Signature: sha256=<hex>
```

A production deployment would put **FastAPI or Flask** in front of the same
`receive()` core (a few lines: read the body, read `X-Signature`, call `receive`,
return `result["status"]`). Swapping the framework changes no security logic.

## Idempotency & out-of-order / late events

- **At-least-once delivery is safe.** The store dedupes on `(source, event_id)`; a
  redelivered id is recorded (`webhook.duplicate` in the audit trail) but never
  fed to the mapping twice.
- **Arrival order does not matter.** The mapper folds events in a canonical order
  (`occurred_at`, then event id), so a late or out-of-order delivery yields the
  identical canonical tables. `replay.py` rebuilds canonical state as a pure
  function of the stored events - replaying the same events in any order produces
  **byte-identical** CSVs and sha256 hashes.

## What is / isn't

- **Is:** a real HMAC-verified, idempotent, append-only ingestion path that feeds
  the canonical layer, with a deterministic replay and a synthetic wallet mapper
  modelled on generic wallet-as-a-service webhook shapes.
- **Isn't:** a real provider integration. The wallet source is synthetic; no live
  webhook endpoint is exposed, and the HTTP layer is stdlib (FastAPI/Flask is the
  documented swap). Public/offline demo posture, like the rest of the repo.

## Tests

```bash
python sources/events/tests/run_tests.py   # offline: no HTTP server, no network, no key
```

Proves: invalid/unsigned signature rejected (with an audit entry); duplicate
delivery is a no-op; replay reproduces identical canonical tables (sha256);
the HMAC rotation window; and the `SourceConnector` shape.
