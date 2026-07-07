"""
sources/events/mappers.py - Per-source event -> canonical mappers.

A mapper turns a source's webhook events into the SAME canonical tables the batch
connectors emit (sources/canonical/schema.py), so finance_core cannot tell whether
a period arrived by pull or by push. One concrete mapper ships:

  WalletProviderMapper - a synthetic "wallet provider" (wallet-as-a-service)
  modelled on generic WaaS webhook shapes (deposit.created, withdrawal.confirmed).
  It produces:
    * cash_bank  - one row per wallet with its reconciled balance
                   (sum of deposits - sum of confirmed withdrawals).
    * payments   - one row per event, for traceability (signed amount).

Determinism regardless of arrival order is the core property: the mapper folds
events in a canonical order (occurred_at, then event id), so a late or out-of-order
delivery produces the identical canonical tables. Balances use Decimal, so there
is no float drift, and are emitted as fixed-precision strings.

Event shape (WaaS-style):
    {"id": "evt_1", "type": "deposit.created",
     "occurred_at": "2026-06-02T10:00:00Z",
     "data": {"wallet_id": "wlt-usd-01", "entity_id": "US",
              "account_name": "Wallet USD Operating", "currency": "USD",
              "amount": "125000.00", "reference": "inv-1001",
              "counterparty": "ACME Corp"}}
"""

from decimal import Decimal, ROUND_HALF_UP


def _dec(x):
    return Decimal(str(x if x not in (None, "") else "0"))


def _money(d):
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _event_sort_key(ev):
    # Canonical order: occurred_at, then event id. Independent of arrival order.
    data = ev.get("data", {})
    return (str(ev.get("occurred_at", "")), str(ev.get("id", "")))


def _txn_date(ev):
    occurred = str(ev.get("occurred_at", ""))
    return occurred[:10] if occurred else ""


class WalletProviderMapper:
    """Synthetic wallet-as-a-service source -> canonical."""

    SOURCE = "wallet"
    DEPOSIT = "deposit.created"
    WITHDRAWAL = "withdrawal.confirmed"
    SUPPORTED = (DEPOSIT, WITHDRAWAL)

    def build_canonical(self, events):
        """Fold wallet events into canonical {cash_bank, payments}. Order-independent."""
        ordered = sorted(events, key=_event_sort_key)

        wallets = {}       # wallet_id -> aggregate record
        payments = []
        for ev in ordered:
            etype = ev.get("type")
            if etype not in self.SUPPORTED:
                continue
            data = ev.get("data", {})
            wid = data.get("wallet_id")
            if not wid:
                continue
            amt = _dec(data.get("amount"))
            signed = amt if etype == self.DEPOSIT else -amt

            w = wallets.setdefault(wid, {
                "account_id": wid,
                "entity_id": data.get("entity_id", ""),
                "account_name": data.get("account_name", wid),
                "bank": data.get("provider", "WalletProvider"),
                "currency": data.get("currency", ""),
                "_balance": Decimal("0"),
            })
            w["_balance"] += signed
            # keep the latest non-empty descriptive fields deterministically
            if data.get("account_name"):
                w["account_name"] = data["account_name"]
            if data.get("entity_id"):
                w["entity_id"] = data["entity_id"]
            if data.get("currency"):
                w["currency"] = data["currency"]

            payments.append({
                "payment_id": ev.get("id"),
                "entity_id": data.get("entity_id", ""),
                "party": data.get("counterparty") or data.get("reference") or "wallet",
                "party_type": "wallet",
                "currency": data.get("currency", ""),
                "amount_local": _money(signed),
                "txn_date": _txn_date(ev),
                "applied_to": data.get("reference", ""),
            })

        cash_bank = []
        for wid in sorted(wallets):
            w = wallets[wid]
            cash_bank.append({
                "account_id": w["account_id"],
                "entity_id": w["entity_id"],
                "account_name": w["account_name"],
                "bank": w["bank"],
                "currency": w["currency"],
                "balance": _money(w["_balance"]),
            })

        payments.sort(key=lambda p: (p["txn_date"], str(p["payment_id"])))
        return {"cash_bank": cash_bank, "payments": payments}


# Per-source registry: source name -> mapper instance. New event sources register
# here; nothing else in the pipeline changes.
_REGISTRY = {
    WalletProviderMapper.SOURCE: WalletProviderMapper(),
}


def get_mapper(source):
    mapper = _REGISTRY.get(source)
    if mapper is None:
        raise KeyError(f"no event mapper registered for source {source!r}")
    return mapper


def register_mapper(source, mapper):
    _REGISTRY[source] = mapper
