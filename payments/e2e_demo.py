"""
payments/e2e_demo.py - The end-to-end governed write path, offline, no API key.

One run exercises all four new modules together, in the order data flows:

  synthetic wallet DEPOSIT webhook (HMAC-signed)          [sources/events/ + config/secrets]
    -> receiver verifies + stores idempotently
    -> replay rebuilds the CANONICAL layer (cash_bank)     [sources/events/replay]
    -> reconciled balance read from canonical              [payments/balances]
    -> Treasury agent PROPOSES a payout (propose-only)     [payments/agent]
    -> deterministic validation (limits/allowlist/...)     [payments/validation]
    -> a human APPROVES via LocalDevIdentity               [identity/ maker-checker]
       (a registered Controller, distinct from the proposer)
    -> SandboxRail EXECUTES (writes the local ledger)      [payments/rails]
    -> every transition in the append-only audit trail     [governance/audit]
  and the EXISTING hard controls still pass unchanged.     [finance_core + O2C]

It also shows the gates biting: an out-of-policy proposal is blocked, and an agent
cannot execute an un-approved proposal.

`run_demo()` returns a structured result the eval harness asserts on
(evals/eval_governed_write.py). Run directly to see it narrated:

    python payments/e2e_demo.py
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CANONICAL = os.path.join(ROOT, "sources", "canonical")
O2C = os.path.join(ROOT, "cfo-office", "o2c")
for _p in (ROOT, HERE, CANONICAL, os.path.join(ROOT, "orchestration"),
           O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import secrets as appsecrets          # noqa: E402
from governance import audit                       # noqa: E402
from identity import providers                     # noqa: E402
from sources.events import signing, receiver, replay  # noqa: E402
from sources.events.store import EventStore         # noqa: E402

import service as service_mod                       # noqa: E402
from service import PaymentService, ApprovalRequired  # noqa: E402
from store import ProposalStore                     # noqa: E402
from balances import ReconciledBalances             # noqa: E402
from rails import SandboxRail                        # noqa: E402
from agent import TreasuryPaymentAgent               # noqa: E402
import model                                         # noqa: E402

WALLET = "wlt-usd-01"
ACME = "cp-acme-bank"
ACME_ACCT = "US64SVBKUS6S3300958879"
HMAC_SECRET = "whsec_e2e_demo_walletsecret_0123456789ab"


def _deposit(event_id, occurred, amount):
    return {"id": event_id, "type": "deposit.created", "occurred_at": occurred,
            "data": {"wallet_id": WALLET, "entity_id": "US",
                     "account_name": "Wallet USD Operating", "currency": "USD",
                     "amount": amount, "reference": "dep-" + event_id, "counterparty": "Customer"}}


def _post(store, event, audit_path, secret=HMAC_SECRET):
    body = json.dumps(event).encode("utf-8")
    sig = signing.compute_signature(secret, body)
    return receiver.receive("wallet", body, sig, store=store, audit_path=audit_path)


def _hard_controls_still_pass(work_dir, verbose):
    """The write path must not weaken any existing hard control. Re-check the close
    integrity controls and the O2C control tower on the clean period."""
    import finance_core as fc
    ctrl = fc.control_checks()
    close_ok = bool(ctrl["books_balanced"]) and ctrl["n_fail"] == 0

    import o2c_orchestrator as o2c
    _, meta_clean = o2c.run(period="2026-06", output_dir=os.path.join(work_dir, "o2c-06"),
                            fail_on_hard=False, verbose=False)
    _, meta_block = o2c.run(period="2026-05", output_dir=os.path.join(work_dir, "o2c-05"),
                            fail_on_hard=False, verbose=False)
    o2c_clean_ok = len(meta_clean["hard_failures"]) == 0            # clean period releases
    o2c_block_ok = len(meta_block["hard_failures"]) > 0             # problem period still blocked
    if verbose:
        print(f"  close integrity controls: books_balanced={ctrl['books_balanced']} "
              f"fails={ctrl['n_fail']}  -> {'PASS' if close_ok else 'FAIL'}")
        print(f"  O2C 2026-06 hard failures: {len(meta_clean['hard_failures'])}  "
              f"-> {'PASS' if o2c_clean_ok else 'FAIL'}")
        print(f"  O2C 2026-05 hard failures: {len(meta_block['hard_failures'])} "
              f"(still blocked) -> {'PASS' if o2c_block_ok else 'FAIL'}")
    return close_ok and o2c_clean_ok and o2c_block_ok


def run_demo(base_dir=None, verbose=True):
    owns_dir = base_dir is None
    base_dir = base_dir or tempfile.mkdtemp(prefix="governed-payment-")
    work = os.path.join(base_dir, "_e2e_run")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)

    store_path = os.path.join(work, "events.jsonl")
    canonical_dir = os.path.join(work, "canonical_out")
    audit_path = os.path.join(work, "audit_trail.jsonl")
    ledger_path = os.path.join(work, "sandbox_ledger.jsonl")
    proposals_path = os.path.join(work, "proposals.json")

    saved = {k: os.environ.get(k) for k in ("WEBHOOK_HMAC_WALLET", "IDENTITY_PROVIDER",
                                            "SECRETS_PROVIDER")}
    os.environ["WEBHOOK_HMAC_WALLET"] = HMAC_SECRET
    os.environ["IDENTITY_PROVIDER"] = "local"
    appsecrets.reset_provider()
    providers.reset_provider()
    local = providers.LocalDevIdentity()

    result = {"steps": {}, "ok": False}
    try:
        def say(msg):
            if verbose:
                print(msg)

        say("=" * 66)
        say("GOVERNED PAYMENT - END-TO-END (offline, no API key)")
        say("=" * 66)

        # 1) wallet deposit webhooks -> receiver -> event store (with a duplicate)
        say("\n[1] Wallet deposit webhooks (HMAC-signed) -> receiver -> event store")
        est = EventStore(store_path)
        deposits = [
            _deposit("evt_d1", "2026-06-01T09:00:00Z", "180000.00"),
            _deposit("evt_d2", "2026-06-05T09:00:00Z", "120000.00"),
        ]
        for d in deposits:
            r = _post(est, d, audit_path)
            say(f"    {d['id']}: accepted={r.accepted} duplicate={r['duplicate']}")
        dup = _post(est, deposits[0], audit_path)          # redelivery
        say(f"    {deposits[0]['id']} redelivered: duplicate={dup['duplicate']} (no re-process)")
        # an unsigned/forged delivery is rejected
        forged = receiver.receive("wallet", json.dumps(_deposit('evt_x', '2026-06-06T00:00:00Z', '999999.00')).encode(),
                                  "sha256=deadbeef", store=est, audit_path=audit_path)
        say(f"    forged delivery: accepted={forged.accepted} reason={forged.get('reason')}")
        result["steps"]["ingestion"] = {
            "stored_events": est.count("wallet"), "duplicate_ignored": dup["duplicate"],
            "forged_rejected": not forged.accepted}

        # 2) replay -> canonical layer
        say("\n[2] Replay event store -> canonical layer (cash_bank)")
        replay.write_canonical(replay.rebuild_canonical(est, "wallet"), canonical_dir, "wallet")
        balances = ReconciledBalances(os.path.join(canonical_dir, "canonical"))
        avail = balances.available(WALLET)
        say(f"    reconciled balance {WALLET}: USD {avail}")
        result["steps"]["canonical"] = {"reconciled_balance": str(avail)}

        # 3) Treasury agent PROPOSES (propose-only)
        say("\n[3] Treasury agent proposes a payout (propose-only)")
        svc = PaymentService(ProposalStore(proposals_path),
                             ReconciledBalances(os.path.join(canonical_dir, "canonical")),
                             SandboxRail(ledger_path), audit_path=audit_path)
        agent = TreasuryPaymentAgent(svc)
        good = agent.propose_payout(
            on_behalf_of_subject="u-treasurer", idempotency_key="e2e-payout-1",
            counterparty_id=ACME, payee_name="ACME Corp", dest_account=ACME_ACCT,
            amount="90000.00", currency="USD", source_account=WALLET, entity_id="US",
            purpose="Q2 vendor settlement", references=("inv-2201",), value_date="2026-06-15")
        say(f"    proposal {good.proposal_id}: state={good.state}")

        # a blocked proposal (over the per-transaction limit)
        blocked = agent.propose_payout(
            on_behalf_of_subject="u-treasurer", idempotency_key="e2e-payout-blocked",
            counterparty_id=ACME, payee_name="ACME Corp", dest_account=ACME_ACCT,
            amount="150000.00", currency="USD", source_account=WALLET, entity_id="US",
            purpose="over-limit", references=("inv-9999",), value_date="2026-06-15")
        say(f"    over-limit proposal {blocked.proposal_id}: state={blocked.state} "
            f"({'; '.join(blocked.validation['reasons'])})")
        good_state_at_propose = good.state       # capture before approve/execute mutate it
        blocked_state = blocked.state
        result["steps"]["proposal"] = {"good_state": good_state_at_propose,
                                       "blocked_state": blocked_state}

        # 4) agent cannot execute directly (gate refuses un-approved)
        say("\n[4] Agent cannot execute directly")
        agent_execute_blocked = False
        try:
            svc.execute(good.proposal_id)
        except ApprovalRequired:
            agent_execute_blocked = True
            say("    execute() before approval -> ApprovalRequired (blocked)")
        result["steps"]["agent_execute_blocked"] = agent_execute_blocked

        # 5) human approval via LocalDevIdentity (Controller, distinct from proposer)
        say("\n[5] Human approval via LocalDevIdentity (maker-checker)")
        controller_token = local.mint_token("u-controller")
        approved = svc.approve(good.proposal_id, controller_token, provider=local,
                               reason="within limits; counterparty allowlisted; funds reconciled")
        say(f"    approved by {approved.approval['subject']} ({approved.approval['name']}), "
            f"role {approved.approval['role']} -> state={approved.state}")
        result["steps"]["approval"] = {"state": approved.state,
                                       "approver": approved.approval["subject"],
                                       "proposer": approved.proposed_by_subject}

        # 6) SandboxRail execution + idempotent replay
        say("\n[6] SandboxRail execution (local ledger) + idempotent replay")
        executed = svc.execute(good.proposal_id, executor="u-controller")
        svc.execute(good.proposal_id, executor="u-controller")   # replay -> no double-pay
        ledger = SandboxRail(ledger_path).ledger()
        say(f"    state={executed.state} rail_ref={executed.execution.get('rail_ref')} "
            f"ledger_rows={len(ledger)} (replay did not double-pay)")
        result["steps"]["execution"] = {"state": executed.state, "ledger_rows": len(ledger)}

        # 7) existing hard controls still pass
        say("\n[7] Existing hard controls still pass (unchanged)")
        controls_ok = _hard_controls_still_pass(work, verbose)
        result["steps"]["hard_controls_pass"] = controls_ok

        # audit trail summary
        actions = [e["action"] for e in audit.read_all(audit_path)]
        result["audit_actions"] = actions
        say(f"\n[audit] {len(actions)} events; "
            f"transitions: {', '.join(sorted(set(a for a in actions if a.startswith('payment.'))))}")

        result["ok"] = (
            result["steps"]["ingestion"]["duplicate_ignored"]
            and result["steps"]["ingestion"]["forged_rejected"]
            and good_state_at_propose == model.VALIDATED
            and blocked_state == model.REJECTED
            and agent_execute_blocked
            and approved.approval["subject"] != approved.proposed_by_subject
            and executed.state == model.EXECUTED
            and len(ledger) == 1
            and controls_ok
            and not service_mod.AUTO_EXECUTE_ENABLED
        )
        result["work_dir"] = work
        say(f"\nRESULT: {'PASS' if result['ok'] else 'FAIL'}   (artifacts: {work})")
        return result
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        appsecrets.reset_provider()
        providers.reset_provider()
        if owns_dir and not verbose:
            shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    res = run_demo(base_dir=HERE, verbose=True)
    sys.exit(0 if res["ok"] else 1)
