"""
eval_governed_write.py - Eval gate for the governed write path (Modules 1-4).

Runs the end-to-end demo (payments/e2e_demo.py) and asserts every governance gate
holds AND that the existing hard controls are unchanged. Exits non-zero on any
failure, so CI fails the moment a gate weakens -- e.g. if an agent could execute,
if an out-of-policy payment slipped through, if a replay double-paid, if the
approval gate were bypassable, if the auto-execute flag defaulted on, or if the
close / O2C hard controls stopped passing.

Deterministic and offline: webhooks are HMAC-signed locally, identity is
LocalDevIdentity, execution is the SandboxRail. No API key, no network.

    python evals/eval_governed_write.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for _p in (ROOT, os.path.join(ROOT, "payments")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import e2e_demo  # noqa: E402  (payments/e2e_demo.py)


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return 1 if ok else 0


def suite_governed_write():
    print("\nSuite - Governed write path (webhook -> canonical -> propose -> approve -> execute)")
    res = e2e_demo.run_demo(base_dir=None, verbose=False)
    s = res["steps"]
    passed = total = 0

    cases = [
        ("webhook: duplicate delivery ignored (no re-process)", s["ingestion"]["duplicate_ignored"]),
        ("webhook: forged signature rejected", s["ingestion"]["forged_rejected"]),
        ("canonical: reconciled balance rebuilt (USD 300000.00)",
         s["canonical"]["reconciled_balance"] == "300000.00"),
        ("proposal: in-policy payout validated", s["proposal"]["good_state"] == "validated"),
        ("proposal: out-of-policy payout blocked", s["proposal"]["blocked_state"] == "rejected"),
        ("gate: agent cannot execute directly", s["agent_execute_blocked"]),
        ("gate: approver distinct from proposer (segregation of duties)",
         s["approval"]["approver"] != s["approval"]["proposer"] and s["approval"]["state"] == "approved"),
        ("execution: settled once, no double-pay on replay",
         s["execution"]["state"] == "executed" and s["execution"]["ledger_rows"] == 1),
        ("existing hard controls still pass (close + O2C)", s["hard_controls_pass"]),
        ("audit trail recorded every payment transition",
         {"payment.proposed", "payment.validated", "payment.signoff.approved",
          "payment.executed"}.issubset(set(res["audit_actions"]))),
        ("overall e2e", res["ok"]),
    ]
    for label, ok in cases:
        total += 1
        passed += _check(label, ok)
    return passed, total


def main():
    print("=" * 60)
    print("GOVERNED WRITE-PATH EVAL - gates must hold")
    print("=" * 60)
    passed, total = suite_governed_write()
    print("-" * 60)
    print(f"  TOTAL {passed}/{total}")
    if passed != total:
        print("\nA governance gate weakened. Exit 1.")
        sys.exit(1)
    print("\nAll governance gates hold.")


if __name__ == "__main__":
    main()
