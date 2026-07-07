"""
payments/store.py - Proposal store: current state + idempotency index.

Holds the current state of every proposal (a mutable snapshot in proposals.json)
and the idempotency index (idempotency_key -> proposal_id). The IMMUTABLE record
of what happened is the append-only governance audit trail (governance/audit.py);
this store is the queryable current-state view, exactly as the self-improvement
system keeps a mutable champions.json alongside the append-only audit trail.

It also answers the two questions the validation engine needs against prior
proposals: the cumulative committed amount per source account per period (for the
per-period limit) and whether an equivalent active proposal already exists (for
duplicate detection).
"""

import json
import os
from decimal import Decimal

from model import PaymentProposal, REJECTED, DENIED, FAILED

# States that no longer commit funds / are not live duplicates.
_INACTIVE = {REJECTED, DENIED, FAILED}


class ProposalStore:
    def __init__(self, path):
        self.path = path
        self._by_id = {}
        self._key_to_id = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        for d in data.get("proposals", []):
            p = PaymentProposal.from_dict(d)
            self._by_id[p.proposal_id] = p
            self._key_to_id[p.idempotency_key] = p.proposal_id

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {"proposals": [p.to_dict() for p in self._by_id.values()]}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)

    def get(self, proposal_id):
        return self._by_id.get(proposal_id)

    def get_by_key(self, idempotency_key):
        pid = self._key_to_id.get(idempotency_key)
        return self._by_id.get(pid) if pid else None

    def put(self, proposal):
        self._by_id[proposal.proposal_id] = proposal
        self._key_to_id[proposal.idempotency_key] = proposal.proposal_id
        self._save()
        return proposal

    def all(self):
        return list(self._by_id.values())

    def committed_in_period(self, source_account, period, exclude_id=None):
        """Sum of amounts for still-committing proposals on this account+period."""
        total = Decimal("0")
        for p in self._by_id.values():
            if p.proposal_id == exclude_id:
                continue
            if p.source_account == source_account and p.period == period \
                    and p.state not in _INACTIVE:
                total += Decimal(str(p.amount or "0"))
        return total

    def find_duplicate(self, proposal):
        """An active proposal with the same economic content (counterparty, amount,
        currency, source account, references) but a different id -> a duplicate."""
        for p in self._by_id.values():
            if p.proposal_id == proposal.proposal_id:
                continue
            if p.state in _INACTIVE:
                continue
            if (p.counterparty_id == proposal.counterparty_id
                    and str(p.amount) == str(proposal.amount)
                    and p.currency == proposal.currency
                    and p.source_account == proposal.source_account
                    and tuple(p.references) == tuple(proposal.references)):
                return p
        return None
