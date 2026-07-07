# payments/ - governed write path (payment initiation)

The rest of the system is deliberately **read-only**. This module adds the first
governed **write** capability without changing that default posture: agents can
only *propose*, a deterministic engine validates, a registered human *approves*,
and only then does an execution adapter move money - into a **local sandbox
ledger**, never a real rail.

```
Treasury agent          deterministic engine        identity/ (maker-checker)      PaymentRail
  propose()  ──►  validate (limits, allowlist,  ──►  approve()  ──►  execute()  ──►  SandboxRail (local ledger)
  (propose-only)   reconciled balance, dup,          (authenticated,   (only if       RealRailStub (not implemented)
                   currency/entity)                   right role,       APPROVED)
                        │                             distinct subject)
                   REJECTED                              every transition ─► governance audit trail
```

## Pieces

| File | Role |
|---|---|
| `model.py` | `PaymentProposal` + the forward-only state machine (`proposed → validated/rejected → approved/denied → executed/failed`). Proposal id is derived from the idempotency key. |
| `policy.py` | The policy **in code**: per-transaction & per-period limits, the allowlisted counterparty registry, and the required approver role per source account. Not mutable state. |
| `balances.py` | `ReconciledBalances` - available balance per account, read from the **canonical** `cash_bank` table (ties to reconciled state, never a live API read). |
| `validation.py` | The deterministic validation engine (pure code, **no LLM**): amount, allowlist + pinned destination, currency/entity consistency, per-transaction & per-period limits, reconciled balance, duplicate detection. |
| `rails.py` | `PaymentRail` interface + `SandboxRail` (idempotent local ledger) + `RealRailStub` (the seam for a bank API / wallet-as-a-service such as **CoinsDo CoinSend** - not implemented). |
| `store.py` | Proposal store: current state + idempotency index (reloadable), per-period committed totals, duplicate lookup. |
| `service.py` | The orchestration and the gates. Holds `AUTO_EXECUTE_ENABLED` (off by default, code-level). |
| `agent.py` | `TreasuryPaymentAgent` - **propose-only** by construction (no execute/approve method exists). May draft the human-readable rationale (the only LLM-eligible field). |

## The guarantees (each proven by a test)

- **Agents are propose-only.** `TreasuryPaymentAgent` has no execute/approve method,
  and `service.execute` refuses anything not `APPROVED` (`ApprovalRequired`).
- **Out-of-policy proposals are blocked.** Over a limit, a non-allowlisted
  counterparty, a wrong destination, a currency/entity mismatch, insufficient
  reconciled balance, a per-period breach, or a duplicate → `REJECTED` with reasons.
- **Idempotency, no double-pay.** A replayed idempotency key returns the existing
  proposal (no new one); a re-`execute` of a settled payment is a no-op; the
  SandboxRail also dedupes on the execution ref. One settlement, one ledger row.
- **Approval gate cannot be bypassed.** Approval goes through `identity/`:
  authenticated, holds the source account's approver role, and a **different
  subject** than the proposer (segregation of duties). Wrong role → `Unauthorized`;
  self-approval → `SegregationOfDutiesError`; execute-before-approve → `ApprovalRequired`.
- **The auto-execute flag can't be flipped from mutable state.** `AUTO_EXECUTE_ENABLED`
  is `False`, assigned in exactly one place (a static test proves the single
  assignment); a full propose→approve→execute cycle never flips it; even forced on,
  the state gate still applies. `maybe_auto_execute` refuses while off.
- **Everything is audited.** Every transition (`payment.proposed`,
  `payment.validated`/`rejected`, `payment.signoff.approved`,
  `payment.executed`/`failed`, and the replay/refusal no-ops) lands in the
  append-only governance audit trail with actor, timestamp, and reason.

## No LLM in the write path

No model call sits in validation, approval, or execution. An LLM may draft a
proposal's `rationale` (prose for the human checker); the validation engine ignores
it, so a persuasive or wrong rationale cannot move a number or bypass a check.

## Honest boundaries

- **SandboxRail is not a real rail.** It writes a local ledger file; no money moves.
- **RealRailStub is not implemented.** `send` raises - it marks exactly where a bank
  API or a wallet-as-a-service (e.g. CoinsDo CoinSend) plugs in, implementing
  `send(proposal, execution_ref) -> {status, rail_ref}` idempotently.
- **Limits/allowlist are illustrative** demo values in `policy.py`.

## Tests

```bash
python payments/tests/run_tests.py    # offline: SandboxRail, LocalDevIdentity, no key
```
