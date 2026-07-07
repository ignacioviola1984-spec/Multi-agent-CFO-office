"""
sources/events/receiver.py - The webhook receiver.

Two layers, deliberately separated:

  * receive(...) - the PURE core. Given a source, the raw request body, and the
    signature header, it verifies the HMAC signature, rejects anything unsigned or
    invalid (with an audit entry), and otherwise stores the event idempotently
    (duplicates recorded, not re-processed). It touches no HTTP objects, so it is
    fully testable offline -- which is where the spec's guarantees are proved.

  * make_server(...) - a minimal HTTP layer over Python's standard-library
    http.server, so the receiver actually runs with NO new dependency and NO
    external service. A production deployment would put FastAPI or Flask in front
    of the SAME receive() core (see README); the security-critical logic lives in
    the core, not the framework.

Endpoint: POST /webhooks/<source>, HMAC signature in the X-Signature header
(sha256=<hex>). Rejections return 401/400; accepted events return 200.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from governance import audit  # noqa: E402
from sources.events import signing  # noqa: E402
from sources.events.store import EventStore  # noqa: E402

SIGNATURE_HEADERS = ("X-Signature", "X-Webhook-Signature")


class ReceiveResult(dict):
    """Plain dict result: {accepted, status, reason, duplicate, event_id, source}."""

    @property
    def accepted(self):
        return self.get("accepted", False)


def receive(source, raw_body, signature_header, store=None, audit_path=None, received_at=None):
    """Verify, reject-or-store one delivery. Returns a ReceiveResult.

    A rejected delivery (missing/invalid signature, unparseable body, missing event
    id) is NEVER stored as processable and is always audited. An accepted delivery
    is stored idempotently; a duplicate id is recorded but not re-processed."""
    store = store if store is not None else EventStore()
    actor = f"webhook:{source}"

    # 1) Signature. Fail closed: unsigned or invalid is rejected + audited.
    if not signing.verify_signature(source, raw_body, signature_header):
        audit.record("webhook.rejected", actor=actor,
                     reason="missing or invalid HMAC signature",
                     path=audit_path, source=source)
        return ReceiveResult(accepted=False, status=401, reason="invalid_signature",
                             source=source)

    # 2) Parse. A signed-but-unparseable body is a provider bug; reject + audit.
    try:
        payload = json.loads(raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body)
    except Exception:  # noqa: BLE001
        audit.record("webhook.rejected", actor=actor, reason="unparseable JSON body",
                     path=audit_path, source=source)
        return ReceiveResult(accepted=False, status=400, reason="unparseable_body",
                             source=source)

    # 3) Event id is mandatory for idempotency.
    event_id = payload.get("id") or payload.get("event_id")
    if not event_id:
        audit.record("webhook.rejected", actor=actor, reason="event has no id",
                     path=audit_path, source=source)
        return ReceiveResult(accepted=False, status=400, reason="missing_event_id",
                             source=source)

    # 4) Store idempotently.
    res = store.append(source, event_id, payload, received_at=received_at, signature_valid=True)
    if res["duplicate"]:
        audit.record("webhook.duplicate", actor=actor,
                     reason=f"duplicate delivery of {event_id} (recorded, not re-processed)",
                     path=audit_path, source=source, event_id=event_id,
                     payload_hash=res["payload_hash"])
        return ReceiveResult(accepted=True, status=200, duplicate=True,
                             event_id=event_id, source=source)

    audit.record("webhook.received", actor=actor,
                 reason=f"event {event_id} ({payload.get('type', 'unknown')}) stored",
                 path=audit_path, source=source, event_id=event_id,
                 payload_hash=res["payload_hash"])
    return ReceiveResult(accepted=True, status=200, duplicate=False,
                         event_id=event_id, source=source)


# --------------------------------------------------------------------------
# Minimal HTTP layer (stdlib only). FastAPI/Flask would wrap receive() the same
# way; the security logic lives in receive(), not here.
# --------------------------------------------------------------------------
def make_server(store=None, host="127.0.0.1", port=8000, audit_path=None):
    """Build (but do not start) an http.server.HTTPServer that routes
    POST /webhooks/<source> to receive(). Call .serve_forever() to run it."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    store = store if store is not None else EventStore()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.path.startswith("/webhooks/"):
                return self._json(404, {"error": "not found"})
            source = self.path[len("/webhooks/"):].strip("/")
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            sig = None
            for h in SIGNATURE_HEADERS:
                sig = self.headers.get(h)
                if sig:
                    break
            res = receive(source, raw, sig, store=store, audit_path=audit_path)
            self._json(res["status"], {k: v for k, v in res.items() if k != "status"})

        def log_message(self, *args):  # keep the test/demo output quiet
            pass

    return HTTPServer((host, port), Handler)


if __name__ == "__main__":  # pragma: no cover - manual/demo use
    import argparse
    ap = argparse.ArgumentParser(description="Run the webhook receiver (stdlib).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    srv = make_server(host=args.host, port=args.port)
    print(f"webhook receiver on http://{args.host}:{args.port}/webhooks/<source>")
    srv.serve_forever()
