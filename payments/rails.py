"""
payments/rails.py - The execution adapter interface (PaymentRail).

Execution is behind an interface so the engine never talks to a bank or a
blockchain directly. Two implementations:

  * SandboxRail  - writes settled payments to a LOCAL ledger file. This is what the
    tests and the demo execute against: real state changes (money "moves" in the
    local ledger), zero external calls. It is idempotent on the execution ref, so
    even if it were called twice it would not double-pay -- defense in depth behind
    the service-level idempotency.

  * RealRailStub - the seam where a real rail plugs in: a bank payment API or a
    wallet-as-a-service (e.g. CoinsDo CoinSend). It is deliberately NOT implemented
    -- `send` raises -- so this can never be mistaken for a live money-movement
    integration. Same honest boundary as the VaultProvider and OIDC stubs.
"""

import datetime
import json
import os
from abc import ABC, abstractmethod


class RailError(Exception):
    """A rail-level failure (declined, unreachable, not implemented)."""


class PaymentRail(ABC):
    name = "abstract"

    @abstractmethod
    def send(self, proposal, execution_ref):
        """Move the money. Returns a result dict with at least {status, rail_ref}.
        `status` is "settled" or "failed". MUST be idempotent on execution_ref."""


class SandboxRail(PaymentRail):
    """Writes settled payments to a local append-only ledger file. No network."""

    name = "sandbox"

    def __init__(self, ledger_path):
        self.ledger_path = ledger_path

    def _ledger(self):
        if not os.path.exists(self.ledger_path):
            return []
        out = []
        with open(self.ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def ledger(self):
        return self._ledger()

    def _find(self, execution_ref):
        for e in self._ledger():
            if e.get("execution_ref") == execution_ref:
                return e
        return None

    def send(self, proposal, execution_ref):
        # Idempotent: if this execution_ref already settled, return it, do not
        # append again (no double-pay), and flag the replay.
        existing = self._find(execution_ref)
        if existing:
            return {"status": "settled", "rail_ref": existing["rail_ref"],
                    "replay": True, "ledger_path": self.ledger_path}
        entry = {
            "rail": self.name,
            "execution_ref": execution_ref,
            "rail_ref": "SBX-" + execution_ref[:16],
            "proposal_id": proposal.proposal_id,
            "counterparty_id": proposal.counterparty_id,
            "dest_account": proposal.dest_account,
            "amount": proposal.amount,
            "currency": proposal.currency,
            "source_account": proposal.source_account,
            "entity_id": proposal.entity_id,
            "settled_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": "settled",
        }
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return {"status": "settled", "rail_ref": entry["rail_ref"], "replay": False,
                "ledger_path": self.ledger_path}


class RealRailStub(PaymentRail):
    """Where a real bank / wallet rail plugs in. Not implemented on purpose."""

    name = "real-stub"

    def __init__(self, provider="unconfigured"):
        self.provider = provider

    def send(self, proposal, execution_ref):
        raise NotImplementedError(
            "RealRailStub does not move real money. Wire a real payment rail here "
            "-- a bank payment API or a wallet-as-a-service such as CoinsDo CoinSend "
            "-- implementing send(proposal, execution_ref) -> {status, rail_ref} "
            "idempotently on execution_ref. Left unimplemented on purpose. See "
            "payments/README.md.")
