"""
payments/agent.py - A propose-only Treasury payment agent.

An agent can DRAFT a payment proposal -- and, if desired, its human-readable
rationale (the one place an LLM is allowed to contribute) -- and submit it to the
PaymentService. It can NEVER validate, approve, or execute: there is simply no
method here that does. That is the "agents are propose-only" guarantee, in code.

The rationale is prose for a human reviewer; the validation engine ignores it
entirely, so even a wrong or persuasive rationale cannot move a number or bypass a
check.
"""


class TreasuryPaymentAgent:
    name = "Treasury"

    def __init__(self, service):
        self._service = service   # can only reach propose(); no execute wrapper exists

    def propose_payout(self, *, on_behalf_of_subject, idempotency_key, counterparty_id,
                       payee_name, dest_account, amount, currency, source_account,
                       entity_id, purpose="", references=(), rationale=None,
                       period="", value_date=""):
        """Draft and submit a payout proposal. Returns the (validated or rejected)
        proposal. Does NOT execute -- execution is a separate, human-gated action on
        the service that this agent has no access to."""
        if rationale is None:
            rationale = self._draft_rationale(payee_name, amount, currency, purpose)
        return self._service.propose(
            agent=self.name, proposed_by_subject=on_behalf_of_subject,
            idempotency_key=idempotency_key, counterparty_id=counterparty_id,
            payee_name=payee_name, dest_account=dest_account, amount=amount,
            currency=currency, source_account=source_account, entity_id=entity_id,
            purpose=purpose, references=tuple(references), rationale=rationale,
            period=period, value_date=value_date)

    @staticmethod
    def _draft_rationale(payee_name, amount, currency, purpose):
        """Deterministic here (offline). In a live system this may be an LLM draft;
        it is prose for the human checker and is never part of validation."""
        tail = f" for {purpose}" if purpose else ""
        return f"Proposed payout of {amount} {currency} to {payee_name}{tail}."
