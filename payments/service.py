"""
payments/service.py - The governed payment write path (the orchestration).

This is where the gates live. The lifecycle is strictly forward-only:

    agent.propose ─► validate ─► (human) approve ─► execute ─► EXECUTED
                       │             │                 │
                       └─REJECTED    └─DENIED          └─FAILED

Guarantees enforced here:
  * Agents are PROPOSE-ONLY. `propose` never executes; there is no code path from a
    proposal to settlement that skips approval.
  * Idempotency. A replayed idempotency key returns the existing proposal (no new
    one); a re-`execute` of a settled payment is a no-op. Neither double-pays.
  * Maker-checker execution gate. `execute` refuses unless the proposal is APPROVED,
    and approval goes through identity/ (authenticated, holds the required role,
    distinct from the proposer). The gate cannot be bypassed.
  * Auto-execution is OFF by default behind AUTO_EXECUTE_ENABLED, a CODE-level flag
    assigned in exactly one place (the same pattern as self-improvement's
    AUTO_ADOPT_ENABLED). Nothing at runtime can flip it from mutable state.
  * Every state transition is written to the append-only governance audit trail
    with actor, timestamp, and reason.

No LLM sits in the validation, approval, or execution path. An LLM may draft a
proposal's human-readable `rationale`; nothing else.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from governance import audit  # noqa: E402
from identity import signoff, access  # noqa: E402
import model  # noqa: E402
import policy as P  # noqa: E402
import validation  # noqa: E402

# Off by default. Executing a validated payment WITHOUT a human approval requires
# deliberately turning this on in code; nothing in the flow reassigns it, and no
# mutable store is read to decide it. (Assigned in exactly one place -- proven by
# the bound test, mirroring AUTO_ADOPT_ENABLED in self-improvement/gate.py.)
AUTO_EXECUTE_ENABLED = False


class PaymentError(Exception):
    """Base for payment write-path failures."""


class InvalidState(PaymentError):
    """The proposal is not in a state that permits the requested transition."""


class ApprovalRequired(PaymentError):
    """Execution attempted without a passed maker-checker approval gate."""


class PaymentService:
    def __init__(self, store, balances, rail, audit_path=None):
        self.store = store
        self.balances = balances
        self.rail = rail
        self.audit_path = audit_path

    def _audit(self, action, actor, reason, **fields):
        return audit.record(action, actor=actor, reason=reason, path=self.audit_path, **fields)

    def _require(self, proposal_id):
        p = self.store.get(proposal_id)
        if p is None:
            raise PaymentError(f"unknown proposal {proposal_id!r}")
        return p

    # ---- 1) propose (agents call this; it never executes) -------------------
    def propose(self, *, agent, proposed_by_subject, idempotency_key, counterparty_id,
                payee_name, dest_account, amount, currency, source_account, entity_id,
                purpose="", references=(), rationale="", period="", value_date=""):
        existing = self.store.get_by_key(idempotency_key)
        if existing is not None:
            self._audit("payment.propose.replay", actor=agent,
                        reason=f"idempotency-key replay -> {existing.proposal_id} "
                               f"(no new proposal, state {existing.state})",
                        proposal_id=existing.proposal_id, idempotency_key=idempotency_key)
            return existing

        proposal = model.PaymentProposal(
            idempotency_key=idempotency_key, proposed_by=agent,
            proposed_by_subject=proposed_by_subject, counterparty_id=counterparty_id,
            payee_name=payee_name, dest_account=dest_account, amount=str(amount),
            currency=currency, source_account=source_account, entity_id=entity_id,
            purpose=purpose, references=tuple(references), rationale=rationale,
            period=period, value_date=value_date)
        self._audit("payment.proposed", actor=agent,
                    reason=f"proposed {amount} {currency} to {counterparty_id} "
                           f"from {source_account}",
                    proposal_id=proposal.proposal_id, amount=str(amount),
                    currency=currency, counterparty=counterparty_id,
                    source_account=source_account, subject=proposed_by_subject)

        result = validation.validate(proposal, self.balances, self.store)
        proposal.validation = result
        if result["passed"]:
            proposal.state = model.VALIDATED
            self._audit("payment.validated", actor="validation-engine",
                        reason="all deterministic checks passed",
                        proposal_id=proposal.proposal_id, checks=result["checks"])
        else:
            proposal.state = model.REJECTED
            self._audit("payment.rejected", actor="validation-engine",
                        reason="; ".join(result["reasons"]),
                        proposal_id=proposal.proposal_id, reasons=result["reasons"])
        self.store.put(proposal)
        return proposal

    # ---- 2) approve / deny (maker-checker, identity-bound) ------------------
    def approve(self, proposal_id, token, *, provider=None, reason=""):
        """Approve a VALIDATED proposal. Goes through identity/: the token must be
        valid, hold the source account's required approver role, and belong to a
        DIFFERENT subject than the proposer. Raises on any of those failures (the
        proposal stays un-approved)."""
        p = self._require(proposal_id)
        if p.state != model.VALIDATED:
            raise InvalidState(
                f"cannot approve {proposal_id} in state {p.state!r} (must be validated)")
        approver_role = P.required_approver_role(p.source_account)
        rec = signoff.record_signoff(
            "payment", proposal_id, approver_role, token, decision="approved",
            reason=reason or f"payment {proposal_id} approved",
            proposer_subject=p.proposed_by_subject, provider=provider,
            audit_path=self.audit_path,
            extra={"amount": str(p.amount), "currency": p.currency,
                   "source_account": p.source_account})
        p.approval = rec
        p.state = model.APPROVED
        self.store.put(p)
        return p

    def deny(self, proposal_id, token, *, provider=None, reason=""):
        p = self._require(proposal_id)
        if p.state != model.VALIDATED:
            raise InvalidState(
                f"cannot deny {proposal_id} in state {p.state!r} (must be validated)")
        approver_role = P.required_approver_role(p.source_account)
        rec = signoff.record_signoff(
            "payment", proposal_id, approver_role, token, decision="rejected",
            reason=reason or f"payment {proposal_id} denied",
            proposer_subject=p.proposed_by_subject, provider=provider,
            audit_path=self.audit_path)
        p.approval = rec
        p.state = model.DENIED
        self.store.put(p)
        return p

    # ---- 3) execute (only after the approval gate) --------------------------
    def execute(self, proposal_id, *, executor="operator"):
        """Execute an APPROVED proposal via the rail. Idempotent: a settled payment
        re-executed is a no-op. Refuses anything not APPROVED -- the approval gate
        cannot be bypassed."""
        p = self._require(proposal_id)

        if p.state == model.EXECUTED:
            self._audit("payment.execute.replay", actor=executor,
                        reason=f"{proposal_id} already executed (idempotent no-op)",
                        proposal_id=proposal_id)
            return p

        if p.state != model.APPROVED:
            raise ApprovalRequired(
                f"cannot execute {proposal_id} in state {p.state!r}: the maker-checker "
                f"approval gate has not been passed")

        # Defense in depth: the approval must be a real, distinct-approver record.
        appr = p.approval or {}
        if appr.get("decision") != "approved":
            raise ApprovalRequired(f"{proposal_id} has no approval record")
        if appr.get("subject") and appr.get("subject") == p.proposed_by_subject:
            raise access.SegregationOfDutiesError(
                f"{proposal_id}: approver equals proposer (should be impossible)")

        execution_ref = p.idempotency_key
        try:
            res = self.rail.send(p, execution_ref)
        except Exception as exc:  # noqa: BLE001 - any rail failure -> FAILED, audited
            p.state = model.FAILED
            p.execution = {"status": "failed", "error": str(exc),
                           "rail": getattr(self.rail, "name", "unknown")}
            self.store.put(p)
            self._audit("payment.failed", actor=executor,
                        reason=f"rail error: {exc}", proposal_id=proposal_id,
                        rail=getattr(self.rail, "name", "unknown"))
            return p

        p.execution = res
        settled = res.get("status") == "settled"
        p.state = model.EXECUTED if settled else model.FAILED
        self.store.put(p)
        self._audit("payment.executed" if settled else "payment.failed", actor=executor,
                    reason=f"rail {self.rail.name} {res.get('status')} "
                           f"ref {res.get('rail_ref')}"
                           + (" (rail replay)" if res.get("replay") else ""),
                    proposal_id=proposal_id, rail=self.rail.name,
                    rail_ref=res.get("rail_ref"), replay=bool(res.get("replay")))
        return p

    # ---- auto path (flag-gated; refused by default) -------------------------
    def maybe_auto_execute(self, proposal_id, actor="auto-executor"):
        """Auto-execute WITHOUT a human approval. Refuses unless AUTO_EXECUTE_ENABLED
        is explicitly on. Mirrors self-improvement's maybe_auto_adopt: the default
        posture is approval-required, and even when enabled the state gate still
        applies."""
        p = self._require(proposal_id)
        if not AUTO_EXECUTE_ENABLED:
            self._audit("payment.auto_execute.refused", actor=actor,
                        reason="auto-execute disabled (human approval required by default)",
                        proposal_id=proposal_id)
            return {"ok": False, "reasons": ["auto-execute disabled (approval required by default)"]}
        if p.state != model.VALIDATED:
            return {"ok": False, "reasons": [f"not in validated state ({p.state})"]}
        p.approval = {"decision": "approved", "mode": "auto", "subject": None,
                      "name": actor, "role": P.required_approver_role(p.source_account)}
        p.state = model.APPROVED
        self.store.put(p)
        self._audit("payment.auto_approved", actor=actor,
                    reason="AUTO_EXECUTE_ENABLED is on (no human approval)",
                    proposal_id=proposal_id)
        self.execute(proposal_id, executor=actor)
        return {"ok": True, "proposal_id": proposal_id}
