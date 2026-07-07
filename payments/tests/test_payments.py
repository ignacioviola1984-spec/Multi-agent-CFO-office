"""
test_payments.py - Deterministic, offline tests for the governed write path.

No network, no real rail, no API key. Proves the spec's guarantees:
  * an agent cannot execute directly;
  * a proposal outside limits / to a non-allowlisted counterparty is blocked;
  * a replayed idempotency key does not double-pay;
  * the approval gate cannot be bypassed;
  * the auto-execute flag cannot be flipped from mutable state.
Plus per-period limits, currency/entity consistency, duplicate detection, and the
identity-bound maker-checker approval (authenticated, right role, distinct subject).
"""

import glob
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PKG = os.path.join(ROOT, "payments")
CANONICAL = os.path.join(ROOT, "sources", "canonical")
for _p in (ROOT, PKG, CANONICAL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import secrets as appsecrets
from identity import providers, access
from governance import audit
import csvio

import model
import service as service_mod
from service import PaymentService, ApprovalRequired
from store import ProposalStore
from balances import ReconciledBalances
from rails import SandboxRail, RealRailStub
from agent import TreasuryPaymentAgent

WALLET = "wlt-usd-01"
ACME = "cp-acme-bank"
ACME_ACCT = "US64SVBKUS6S3300958879"


def _write_cash_bank(data_dir, balance="200000.00"):
    rows = [{"account_id": WALLET, "entity_id": "US",
             "account_name": "Wallet USD Operating", "bank": "WalletProvider",
             "currency": "USD", "balance": balance}]
    csvio.write_table(os.path.join(data_dir, "cash_bank.csv"),
                      ["account_id", "entity_id", "account_name", "bank", "currency", "balance"],
                      rows)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "IDENTITY_PROVIDER", "SECRETS_PROVIDER", "LOCAL_IDENTITY_SIGNING_KEY")}
        for k in self._saved:
            os.environ.pop(k, None)
        appsecrets.reset_provider()
        providers.reset_provider()
        self.local = providers.LocalDevIdentity()
        self.tmp = tempfile.mkdtemp()
        self.canonical = os.path.join(self.tmp, "canonical")
        os.makedirs(self.canonical, exist_ok=True)
        _write_cash_bank(self.canonical)
        self.audit_path = os.path.join(self.tmp, "trail.jsonl")
        self.ledger_path = os.path.join(self.tmp, "sandbox_ledger.jsonl")
        self.store_path = os.path.join(self.tmp, "proposals.json")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        appsecrets.reset_provider()
        providers.reset_provider()

    def _service(self):
        return PaymentService(ProposalStore(self.store_path),
                              ReconciledBalances(self.canonical),
                              SandboxRail(self.ledger_path),
                              audit_path=self.audit_path)

    def _agent(self, svc):
        return TreasuryPaymentAgent(svc)

    def _propose(self, agent, *, key="idem-1", counterparty_id=ACME, dest=ACME_ACCT,
                 amount="50000.00", currency="USD", source=WALLET, entity="US",
                 references=("inv-1",)):
        return agent.propose_payout(
            on_behalf_of_subject="u-treasurer", idempotency_key=key,
            counterparty_id=counterparty_id, payee_name="ACME Corp", dest_account=dest,
            amount=amount, currency=currency, source_account=source, entity_id=entity,
            purpose="vendor invoice", references=references, value_date="2026-06-15")

    def _approve(self, svc, pid, subject="u-controller"):
        tok = self.local.mint_token(subject)
        return svc.approve(pid, tok, provider=self.local)


class TestHappyPath(Base):
    def test_full_lifecycle(self):
        svc = self._service()
        agent = self._agent(svc)
        p = self._propose(agent)
        self.assertEqual(p.state, model.VALIDATED)
        self._approve(svc, p.proposal_id)
        p = svc.execute(p.proposal_id, executor="u-controller")
        self.assertEqual(p.state, model.EXECUTED)
        self.assertEqual(len(SandboxRail(self.ledger_path).ledger()), 1)
        # audit trail carries every transition
        actions = {e["action"] for e in audit.read_all(self.audit_path)}
        self.assertLessEqual({"payment.proposed", "payment.validated",
                              "payment.signoff.approved", "payment.executed"}, actions)


class TestAgentCannotExecute(Base):
    def test_agent_has_no_execute(self):
        svc = self._service()
        agent = self._agent(svc)
        self.assertFalse(hasattr(agent, "execute"))
        self.assertFalse(hasattr(agent, "approve"))

    def test_execute_before_approval_is_refused(self):
        svc = self._service()
        p = self._propose(self._agent(svc))
        self.assertEqual(p.state, model.VALIDATED)
        with self.assertRaises(ApprovalRequired):
            svc.execute(p.proposal_id)          # validated, not approved
        self.assertEqual(SandboxRail(self.ledger_path).ledger(), [])


class TestValidationBlocks(Base):
    def _rejected_reasons(self, p):
        self.assertEqual(p.state, model.REJECTED)
        return " ".join(p.validation["reasons"]).lower()

    def test_over_per_transaction_limit_blocked(self):
        p = self._propose(self._agent(self._service()), amount="150000.00", key="k-lim")
        self.assertIn("per-transaction limit", self._rejected_reasons(p))

    def test_non_allowlisted_counterparty_blocked(self):
        p = self._propose(self._agent(self._service()), counterparty_id="cp-evil",
                          dest="0xEVIL", key="k-cp")
        self.assertIn("not allowlisted", self._rejected_reasons(p))

    def test_wrong_destination_blocked(self):
        p = self._propose(self._agent(self._service()), dest="US00WRONGACCOUNT", key="k-dst")
        self.assertIn("destination account", self._rejected_reasons(p))

    def test_currency_mismatch_blocked(self):
        p = self._propose(self._agent(self._service()), currency="EUR", key="k-ccy")
        self.assertIn("currency", self._rejected_reasons(p))

    def test_insufficient_reconciled_balance_blocked(self):
        # balance is 200k; ask for 90k twice -> second exceeds remaining? No: balance
        # check is per-proposal vs full balance. Use a fresh account with low balance.
        _write_cash_bank(self.canonical, balance="10000.00")
        p = self._propose(self._agent(self._service()), amount="50000.00", key="k-bal")
        self.assertIn("reconciled balance", self._rejected_reasons(p))

    def test_per_period_limit_blocked(self):
        svc = self._service()
        agent = self._agent(svc)
        # Three 90k proposals in the same period/account: 90+90 ok (180<=250), the
        # third pushes cumulative to 270 > 250 -> blocked.
        a = self._propose(agent, amount="90000.00", key="pp-1", references=("r1",))
        b = self._propose(agent, amount="90000.00", key="pp-2", references=("r2",))
        self.assertEqual(a.state, model.VALIDATED)
        self.assertEqual(b.state, model.VALIDATED)
        c = self._propose(agent, amount="90000.00", key="pp-3", references=("r3",))
        self.assertIn("per-period limit", self._rejected_reasons(c))

    def test_duplicate_detection_blocks(self):
        svc = self._service()
        agent = self._agent(svc)
        self._propose(agent, key="dup-1", references=("same-ref",))
        dup = self._propose(agent, key="dup-2", references=("same-ref",))  # same economics, new key
        self.assertIn("duplicate", self._rejected_reasons(dup))


class TestIdempotency(Base):
    def test_replayed_key_no_new_proposal(self):
        svc = self._service()
        agent = self._agent(svc)
        p1 = self._propose(agent, key="same-key")
        p2 = self._propose(agent, key="same-key")
        self.assertEqual(p1.proposal_id, p2.proposal_id)
        self.assertEqual(len(svc.store.all()), 1)
        self.assertTrue(audit.entries_where(self.audit_path, action="payment.propose.replay"))

    def test_reexecute_does_not_double_pay(self):
        svc = self._service()
        p = self._propose(self._agent(svc), key="exec-key")
        self._approve(svc, p.proposal_id)
        svc.execute(p.proposal_id)
        svc.execute(p.proposal_id)          # replay
        svc.execute(p.proposal_id)          # replay again
        self.assertEqual(len(SandboxRail(self.ledger_path).ledger()), 1)
        self.assertTrue(audit.entries_where(self.audit_path, action="payment.execute.replay"))


class TestApprovalGate(Base):
    def test_wrong_role_cannot_approve(self):
        svc = self._service()
        p = self._propose(self._agent(svc), key="wr-role")
        tok = self.local.mint_token("u-tax-mgr")     # Tax Manager, not Controller
        with self.assertRaises(access.Unauthorized):
            svc.approve(p.proposal_id, tok, provider=self.local)
        self.assertEqual(svc.store.get(p.proposal_id).state, model.VALIDATED)  # not approved

    def test_proposer_cannot_self_approve(self):
        # Make the proposer also hold the approver role, then try to approve.
        svc = self._service()
        agent = self._agent(svc)
        p = agent.propose_payout(
            on_behalf_of_subject="u-controller",   # proposer IS a Controller
            idempotency_key="self-appr", counterparty_id=ACME, payee_name="ACME Corp",
            dest_account=ACME_ACCT, amount="50000.00", currency="USD",
            source_account=WALLET, entity_id="US", references=("s1",))
        tok = self.local.mint_token("u-controller")
        with self.assertRaises(access.SegregationOfDutiesError):
            svc.approve(p.proposal_id, tok, provider=self.local)
        self.assertEqual(svc.store.get(p.proposal_id).state, model.VALIDATED)

    def test_unauthenticated_cannot_approve(self):
        svc = self._service()
        p = self._propose(self._agent(svc), key="unauth")
        with self.assertRaises(access.Unauthenticated):
            svc.approve(p.proposal_id, "", provider=self.local)


class TestAutoExecuteFlag(Base):
    def test_flag_off_by_default(self):
        self.assertFalse(service_mod.AUTO_EXECUTE_ENABLED)

    def test_maybe_auto_execute_refused_while_off(self):
        svc = self._service()
        p = self._propose(self._agent(svc), key="auto-off")
        res = svc.maybe_auto_execute(p.proposal_id)
        self.assertFalse(res["ok"])
        self.assertEqual(svc.store.get(p.proposal_id).state, model.VALIDATED)
        self.assertEqual(SandboxRail(self.ledger_path).ledger(), [])
        self.assertTrue(audit.entries_where(self.audit_path, action="payment.auto_execute.refused"))

    def test_flag_assigned_in_exactly_one_place(self):
        # Static proof it cannot be flipped from mutable state: AUTO_EXECUTE_ENABLED
        # is ASSIGNED in exactly one place across payments/*.py (the module default).
        assigns = 0
        for f in glob.glob(os.path.join(PKG, "*.py")):
            with open(f, encoding="utf-8") as fh:
                assigns += len(re.findall(r"AUTO_EXECUTE_ENABLED\s*=(?!=)", fh.read()))
        self.assertEqual(assigns, 1)

    def test_full_cycle_never_flips_flag(self):
        svc = self._service()
        p = self._propose(self._agent(svc), key="cycle")
        self._approve(svc, p.proposal_id)
        svc.execute(p.proposal_id)
        self.assertFalse(service_mod.AUTO_EXECUTE_ENABLED)

    def test_even_when_enabled_state_gate_applies(self):
        svc = self._service()
        # A rejected proposal cannot be auto-executed even if the flag is forced on.
        p = self._propose(self._agent(svc), amount="150000.00", key="enabled-guard")  # over limit -> rejected
        service_mod.AUTO_EXECUTE_ENABLED = True
        try:
            res = svc.maybe_auto_execute(p.proposal_id)
            self.assertFalse(res["ok"])
            self.assertEqual(SandboxRail(self.ledger_path).ledger(), [])
        finally:
            service_mod.AUTO_EXECUTE_ENABLED = False


class TestRealRailStub(Base):
    def test_real_rail_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            RealRailStub().send(object(), "ref")


if __name__ == "__main__":
    unittest.main(verbosity=2)
