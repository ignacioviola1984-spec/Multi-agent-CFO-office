"""
sources/events/store.py - Idempotent, append-only event store.

Every accepted webhook is persisted append-only with its source, event id,
received-at timestamp, and a sha256 payload hash. Idempotency is keyed on
(source, event_id): a duplicate delivery is RECORDED (so redelivery is visible in
the trail) but never re-processed -- only the first occurrence of an id feeds the
canonical mapping. This is what makes at-least-once webhook delivery safe.

The store is redaction-aware: payloads pass through config.secrets.redact_obj
before they are written, so a secret can never be persisted even if a provider
puts one in a payload. Payloads are otherwise stored verbatim so replay is exact.

Append-only in the strict sense: entries are only ever appended; there is no
update or delete method. The default file is sources/events/_store/events.jsonl
(gitignored); the path is overridable so tests and replay use isolated stores.
"""

import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets  # noqa: E402

DEFAULT_STORE = os.path.join(HERE, "_store", "events.jsonl")


def canonical_json(obj):
    """Deterministic JSON: sorted keys, no incidental whitespace. Used for hashing
    and for stable serialization."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class EventStore:
    def __init__(self, path=None):
        self.path = path or DEFAULT_STORE
        self._index = set()          # (source, event_id) seen at least once
        self._first = {}             # (source, event_id) -> first stored record
        self._order = []             # (source, event_id) in first-seen order
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = (rec.get("source"), rec.get("event_id"))
                if rec.get("duplicate"):
                    continue  # duplicate markers do not define first occurrence
                if key not in self._index:
                    self._index.add(key)
                    self._first[key] = rec
                    self._order.append(key)

    def has(self, source, event_id):
        return (source, event_id) in self._index

    def _append_line(self, rec):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(canonical_json(rec) + "\n")

    def append(self, source, event_id, payload, received_at=None, signature_valid=True):
        """Persist an event idempotently. Returns a result dict:
        {stored, duplicate, source, event_id, payload_hash}. A duplicate id is
        appended as a duplicate MARKER (audit-visible) but not re-processed."""
        redacted = appsecrets.redact_obj(payload)
        phash = payload_hash(redacted)
        key = (source, event_id)
        if key in self._index:
            self._append_line({
                "source": source, "event_id": event_id, "duplicate": True,
                "received_at": received_at or _now_iso(), "payload_hash": phash,
            })
            return {"stored": False, "duplicate": True, "source": source,
                    "event_id": event_id, "payload_hash": phash}
        rec = {
            "source": source, "event_id": event_id, "duplicate": False,
            "received_at": received_at or _now_iso(), "payload_hash": phash,
            "signature_valid": bool(signature_valid), "payload": redacted,
        }
        self._append_line(rec)
        self._index.add(key)
        self._first[key] = rec
        self._order.append(key)
        return {"stored": True, "duplicate": False, "source": source,
                "event_id": event_id, "payload_hash": phash}

    def events(self, source=None):
        """First occurrence of every (source, event_id), in first-seen order.
        Filtered by source when given. These are the events replay consumes."""
        out = []
        for key in self._order:
            rec = self._first[key]
            if source is None or rec.get("source") == source:
                out.append(rec)
        return out

    def payloads(self, source):
        return [r["payload"] for r in self.events(source)]

    def count(self, source=None):
        return len(self.events(source))
