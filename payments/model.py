"""
payments/model.py - The PaymentProposal and its state machine.

A proposal is the ONLY thing an agent can create. It carries what a payment needs
(payee, amount, currency, source account/wallet, purpose, references) plus the
governance fields (idempotency key, proposer identity, state, and the validation /
approval / execution records appended as it moves through the gates). An agent
never sets state past PROPOSED; state only advances through the service's gates.

The proposal id is DERIVED from the idempotency key (a stable hash), so the same
key always maps to the same proposal -- the basis for replay-safety.

Amounts are strings here and parsed to Decimal in the validation engine, so no
float drift ever enters a money figure.
"""

import dataclasses
import datetime
import hashlib
from dataclasses import dataclass, field

# --- states (a strict, forward-only machine) ---------------------------------
PROPOSED = "proposed"
VALIDATED = "validated"
REJECTED = "rejected"
APPROVED = "approved"
DENIED = "denied"
EXECUTED = "executed"
FAILED = "failed"

# Legal transitions. Enforced by the service; there is no path proposed->executed.
TRANSITIONS = {
    PROPOSED: {VALIDATED, REJECTED},
    VALIDATED: {APPROVED, DENIED},
    REJECTED: set(),
    APPROVED: {EXECUTED, FAILED},
    DENIED: set(),
    EXECUTED: set(),
    FAILED: set(),          # terminal: a failed execution is not silently retried
}

TERMINAL = {REJECTED, DENIED, EXECUTED, FAILED}


def derive_proposal_id(idempotency_key):
    return "PMT-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass
class PaymentProposal:
    idempotency_key: str
    proposed_by: str                 # the agent that drafted it (maker)
    proposed_by_subject: str         # the identity subject on whose behalf (for SoD)
    counterparty_id: str             # must resolve in the allowlist registry
    payee_name: str
    dest_account: str                # IBAN / wallet address; checked against allowlist
    amount: str                      # native currency, as a string (parsed to Decimal)
    currency: str
    source_account: str              # wallet / bank account id the funds leave
    entity_id: str
    purpose: str = ""
    references: tuple = field(default_factory=tuple)
    rationale: str = ""              # human-readable; MAY be LLM-drafted. Never validated.
    period: str = ""                 # YYYY-MM for the per-period limit; default from value_date
    value_date: str = ""
    proposal_id: str = ""
    state: str = PROPOSED
    validation: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self):
        if not self.proposal_id:
            self.proposal_id = derive_proposal_id(self.idempotency_key)
        if not self.value_date:
            self.value_date = datetime.date.today().isoformat()
        if not self.period:
            self.period = self.value_date[:7]
        self.references = tuple(self.references)

    def to_dict(self):
        d = dataclasses.asdict(self)
        d["references"] = list(self.references)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["references"] = tuple(d.get("references", ()))
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
