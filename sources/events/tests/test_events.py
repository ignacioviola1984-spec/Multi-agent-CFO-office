"""
test_events.py - Deterministic, offline tests for sources/events/ (webhooks).

No network, no HTTP server, no API key: the receiver's PURE core is exercised
directly. Proves the spec's requirements:
  * an invalid / missing signature is rejected (with an audit entry);
  * a duplicate delivery is a no-op (recorded, not re-processed);
  * replay from the event store reproduces identical canonical tables (sha256),
    regardless of arrival order.
Plus the HMAC dual-secret rotation window and the SourceConnector shape.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets
from governance import audit
from sources.events import signing, receiver, replay
from sources.events.store import EventStore
from sources.events.mappers import WalletProviderMapper
from sources.events.connector import EventSourceConnector

SECRET = "whsec_walletsecret_0123456789abcdef"
SOURCE = "wallet"


def _events():
    return [
        {"id": "evt_1", "type": "deposit.created", "occurred_at": "2026-06-01T09:00:00Z",
         "data": {"wallet_id": "wlt-usd-01", "entity_id": "US",
                  "account_name": "Wallet USD Operating", "currency": "USD",
                  "amount": "125000.00", "reference": "inv-1001", "counterparty": "ACME"}},
        {"id": "evt_2", "type": "deposit.created", "occurred_at": "2026-06-02T09:00:00Z",
         "data": {"wallet_id": "wlt-usd-01", "entity_id": "US",
                  "account_name": "Wallet USD Operating", "currency": "USD",
                  "amount": "25000.00", "reference": "inv-1002", "counterparty": "Beta"}},
        {"id": "evt_3", "type": "withdrawal.confirmed", "occurred_at": "2026-06-03T09:00:00Z",
         "data": {"wallet_id": "wlt-usd-01", "entity_id": "US",
                  "account_name": "Wallet USD Operating", "currency": "USD",
                  "amount": "40000.00", "reference": "pay-9001", "counterparty": "Vendor X"}},
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "SECRETS_PROVIDER", "WEBHOOK_HMAC_WALLET", "WEBHOOK_HMAC_WALLET_NEXT")}
        for k in self._saved:
            os.environ.pop(k, None)
        os.environ["WEBHOOK_HMAC_WALLET"] = SECRET
        appsecrets.reset_provider()
        self.tmp = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmp, "events.jsonl")
        self.audit_path = os.path.join(self.tmp, "trail.jsonl")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        appsecrets.reset_provider()

    def _post(self, store, event, secret=SECRET, sign=True):
        body = json.dumps(event).encode("utf-8")
        sig = signing.compute_signature(secret, body) if sign else None
        return receiver.receive(SOURCE, body, sig, store=store, audit_path=self.audit_path)


class TestSigning(Base):
    def test_valid_signature_verifies(self):
        body = b'{"id":"x"}'
        self.assertTrue(signing.verify_signature(SOURCE, body, signing.compute_signature(SECRET, body)))

    def test_wrong_secret_fails(self):
        body = b'{"id":"x"}'
        self.assertFalse(signing.verify_signature(SOURCE, body, signing.compute_signature("nope-secret-xxxx", body)))

    def test_missing_header_fails(self):
        self.assertFalse(signing.verify_signature(SOURCE, b'{"id":"x"}', None))

    def test_dual_secret_rotation_window(self):
        os.environ["WEBHOOK_HMAC_WALLET_NEXT"] = "whsec_new_rotationsecret_abcdef"
        appsecrets.reset_provider()
        body = b'{"id":"x"}'
        # signed with the NEXT secret, still accepted while the window is open
        sig = signing.compute_signature("whsec_new_rotationsecret_abcdef", body)
        self.assertTrue(signing.verify_signature(SOURCE, body, sig))


class TestReceiver(Base):
    def test_invalid_signature_rejected_and_audited(self):
        store = EventStore(self.store_path)
        res = self._post(store, _events()[0], secret="wrong-secret-abcdef")
        self.assertFalse(res.accepted)
        self.assertEqual(res["reason"], "invalid_signature")
        self.assertEqual(store.count(SOURCE), 0)   # nothing processable stored
        self.assertEqual(len(audit.entries_where(self.audit_path, action="webhook.rejected")), 1)

    def test_unsigned_rejected(self):
        store = EventStore(self.store_path)
        res = self._post(store, _events()[0], sign=False)
        self.assertFalse(res.accepted)
        self.assertEqual(store.count(SOURCE), 0)

    def test_missing_event_id_rejected(self):
        store = EventStore(self.store_path)
        res = self._post(store, {"type": "deposit.created", "data": {}})
        self.assertFalse(res.accepted)
        self.assertEqual(res["reason"], "missing_event_id")

    def test_valid_event_accepted_and_stored(self):
        store = EventStore(self.store_path)
        res = self._post(store, _events()[0])
        self.assertTrue(res.accepted)
        self.assertFalse(res["duplicate"])
        self.assertEqual(store.count(SOURCE), 1)
        self.assertEqual(len(audit.entries_where(self.audit_path, action="webhook.received")), 1)

    def test_duplicate_delivery_is_noop(self):
        store = EventStore(self.store_path)
        self._post(store, _events()[0])
        res2 = self._post(store, _events()[0])           # same id redelivered
        self.assertTrue(res2.accepted)
        self.assertTrue(res2["duplicate"])
        self.assertEqual(store.count(SOURCE), 1)          # NOT re-processed
        self.assertEqual(len(audit.entries_where(self.audit_path, action="webhook.duplicate")), 1)

    def test_store_survives_reload(self):
        store = EventStore(self.store_path)
        for e in _events():
            self._post(store, e)
        # a fresh store over the same file sees the same processable events
        reopened = EventStore(self.store_path)
        self.assertEqual(reopened.count(SOURCE), 3)


class TestMapper(Base):
    def test_balance_and_payments(self):
        tables = WalletProviderMapper().build_canonical(_events())
        self.assertEqual(len(tables["cash_bank"]), 1)
        row = tables["cash_bank"][0]
        self.assertEqual(row["account_id"], "wlt-usd-01")
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["balance"], "110000.00")     # 125000 + 25000 - 40000
        self.assertEqual(len(tables["payments"]), 3)
        # withdrawal is a signed (negative) payment
        wd = [p for p in tables["payments"] if p["payment_id"] == "evt_3"][0]
        self.assertEqual(wd["amount_local"], "-40000.00")

    def test_order_independent(self):
        import random
        evs = _events()
        shuffled = list(reversed(evs))
        a = WalletProviderMapper().build_canonical(evs)
        b = WalletProviderMapper().build_canonical(shuffled)
        self.assertEqual(a, b)


class TestReplayIdentical(Base):
    def _store_with(self, order):
        path = os.path.join(tempfile.mkdtemp(), "events.jsonl")
        store = EventStore(path)
        for e in order:
            self._post(store, e)
        return store

    def _hashes(self, out_dir):
        can = os.path.join(out_dir, "canonical")
        h = {}
        for fn in sorted(os.listdir(can)):
            h[fn] = hashlib.sha256(open(os.path.join(can, fn), "rb").read()).hexdigest()
        return h

    def test_replay_reproduces_identical_canonical(self):
        evs = _events()
        s_in_order = self._store_with(evs)
        s_shuffled = self._store_with([evs[2], evs[0], evs[1]])   # out-of-order arrival
        out1 = os.path.join(self.tmp, "run_ordered")
        out2 = os.path.join(self.tmp, "run_shuffled")
        replay.write_canonical(replay.rebuild_canonical(s_in_order, SOURCE), out1, SOURCE)
        replay.write_canonical(replay.rebuild_canonical(s_shuffled, SOURCE), out2, SOURCE)
        self.assertEqual(self._hashes(out1), self._hashes(out2))   # byte-identical


class TestConnectorShape(Base):
    def test_event_source_connector_emits_cash_bank(self):
        store = EventStore(self.store_path)
        for e in _events():
            self._post(store, e)
        conn = EventSourceConnector(store=store, source=SOURCE)
        cash = conn.fetch_cash_bank()
        self.assertEqual(cash[0]["balance"], "110000.00")
        # honors the contract: the full canonical table set is present
        tables = conn.canonical_tables()
        self.assertIn("ar_invoices", tables)   # empty, but present
        self.assertIn("payments", tables)


if __name__ == "__main__":
    unittest.main(verbosity=2)
