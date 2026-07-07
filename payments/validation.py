"""
payments/validation.py - The deterministic validation engine. Pure code, no LLM.

Given a proposal (plus the reconciled balances and the store of prior proposals),
it runs every hard check and returns a structured result. NOTHING here calls a
model; an LLM may draft a proposal's human-readable rationale, but it never touches
this path. A proposal passes only if EVERY check passes; any failure lists a
reason and the proposal is rejected.

Checks:
  1. amount is a positive money value;
  2. counterparty is allowlisted AND the destination account/address matches the
     pinned one exactly;
  3. currency + entity consistency (proposal vs allowlist vs source account);
  4. per-transaction limit;
  5. per-period cumulative limit (this proposal + prior committing proposals on the
     same source account and period);
  6. sufficient RECONCILED balance (from the canonical layer, not a live API);
  7. duplicate detection (an equivalent active proposal already exists).
"""

from decimal import Decimal, InvalidOperation

import policy as P


def _parse_amount(raw):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def validate(proposal, balances, store):
    """Return {passed, reasons, checks}. Deterministic; no side effects."""
    reasons = []
    checks = {}

    amount = _parse_amount(proposal.amount)

    # 1) positive amount
    if amount is None or amount <= 0:
        reasons.append(f"amount {proposal.amount!r} is not a positive value")
        checks["amount_positive"] = False
        # Without a valid amount the numeric checks are meaningless; stop early but
        # still report the structural checks below.
        amount = None
    else:
        checks["amount_positive"] = True

    # 2) allowlist + pinned destination
    cp = P.counterparty(proposal.counterparty_id)
    if cp is None:
        reasons.append(f"counterparty {proposal.counterparty_id!r} is not allowlisted")
        checks["counterparty_allowlisted"] = False
    else:
        checks["counterparty_allowlisted"] = True
        if proposal.dest_account != cp["account"]:
            reasons.append(
                f"destination account does not match the allowlisted counterparty "
                f"{proposal.counterparty_id!r}")
            checks["destination_matches_allowlist"] = False
        else:
            checks["destination_matches_allowlist"] = True

    # 3) currency / entity consistency
    ccy_ok = True
    if cp is not None:
        if proposal.currency != cp["currency"]:
            reasons.append(
                f"currency {proposal.currency!r} != counterparty currency {cp['currency']!r}")
            ccy_ok = False
        if proposal.entity_id != cp["entity_id"]:
            reasons.append(
                f"entity {proposal.entity_id!r} != counterparty entity {cp['entity_id']!r}")
            ccy_ok = False
    acct_ccy = balances.currency(proposal.source_account)
    if acct_ccy is not None and proposal.currency != acct_ccy:
        reasons.append(
            f"currency {proposal.currency!r} != source account currency {acct_ccy!r}")
        ccy_ok = False
    checks["currency_entity_consistent"] = ccy_ok

    # 4) per-transaction limit
    if amount is not None:
        within_txn = amount <= P.PER_TRANSACTION_LIMIT
        checks["within_per_transaction_limit"] = within_txn
        if not within_txn:
            reasons.append(
                f"amount {amount} exceeds per-transaction limit {P.PER_TRANSACTION_LIMIT}")

        # 5) per-period cumulative limit
        prior = store.committed_in_period(proposal.source_account, proposal.period,
                                          exclude_id=proposal.proposal_id)
        cumulative = prior + amount
        within_period = cumulative <= P.PER_PERIOD_LIMIT
        checks["within_per_period_limit"] = within_period
        if not within_period:
            reasons.append(
                f"period total {cumulative} (prior {prior} + {amount}) exceeds "
                f"per-period limit {P.PER_PERIOD_LIMIT} for {proposal.source_account}")

        # 6) sufficient reconciled balance
        if not balances.known(proposal.source_account):
            reasons.append(
                f"source account {proposal.source_account!r} not found in reconciled "
                f"canonical balances")
            checks["sufficient_reconciled_balance"] = False
        else:
            available = balances.available(proposal.source_account)
            enough = amount <= available
            checks["sufficient_reconciled_balance"] = enough
            if not enough:
                reasons.append(
                    f"amount {amount} exceeds reconciled balance {available} on "
                    f"{proposal.source_account}")

    # 7) duplicate detection
    dup = store.find_duplicate(proposal)
    if dup is not None:
        reasons.append(f"duplicate of active proposal {dup.proposal_id} "
                       f"(same counterparty/amount/currency/source/references)")
        checks["not_duplicate"] = False
    else:
        checks["not_duplicate"] = True

    return {"passed": not reasons, "reasons": reasons, "checks": checks}
