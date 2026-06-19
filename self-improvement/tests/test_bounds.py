"""
test_bounds.py - Prove the bounds. This is the demonstrable safety core.

Each test runs against an ISOLATED temp champion store (SELFIMPROVE_STATE_DIR),
so nothing here touches the real model state. The tests prove that the
self-improvement system can ONLY change registry parameters, within their
bounds, and only after the evals plus a human approval, and that everything is
audited and reversible.

Run:  python self-improvement/tests/test_bounds.py
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import registry
import propose as proposer
import gate as gate_mod
import rollback as rollback_mod
import audit

ROOT = os.path.join(PKG, "..")
EVAL_SET = os.path.join(ROOT, "evals", "eval_set.py")

# Outcomes that calibrate ar_collection_rate to exactly 0.92 (in bounds, in step).
AR_OUTCOMES_092 = [{"forecast_collectible": 1000, "actual_collected": 920}]
# Outcomes whose raw realized rate (1.30) blows past every bound -> must clamp.
AR_OUTCOMES_RAW_130 = [{"forecast_collectible": 1000, "actual_collected": 1300}]


def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _inject(param, proposed, by="proposer", outcomes=None):
    """Append a hand-built proposal (simulating a malformed/tampered challenger)."""
    store = proposer.load_proposals()
    store["seq"] += 1
    pid = f"P{store['seq']}"
    store["items"].append({
        "id": pid, "param": param, "old": registry.champion_value(param),
        "proposed": proposed, "raw_candidate": proposed, "evidence": {},
        "rationale": "(injected)", "status": "pending", "by": by,
        "outcomes": outcomes or [],
    })
    proposer.save_proposals(store)
    return pid


class BoundsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="selfimprove_test_")
        os.environ["SELFIMPROVE_STATE_DIR"] = self.tmp
        registry.ensure_init()
        self.eval_hash_before = _file_hash(EVAL_SET)

    def tearDown(self):
        os.environ.pop("SELFIMPROVE_STATE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 1) A proposal outside [min, max] is rejected.
    def test_out_of_bounds_rejected(self):
        pid = _inject("ar_collection_rate", 1.05)
        res = gate_mod.approve(pid, approver="Treasurer")
        self.assertFalse(res["ok"])
        self.assertTrue(any("out of bounds" in r for r in res["reasons"]))
        self.assertEqual(registry.champion_value("ar_collection_rate"), 0.90)

    # 2) A proposal exceeding max_step is clamped (proposer) or rejected (gate).
    def test_max_step_clamped_or_rejected(self):
        # proposer clamps a raw 1.30 down to the 0.93 step cap
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_RAW_130)
        self.assertEqual(p["proposed"], 0.93)
        self.assertTrue(registry.within_step("ar_collection_rate", p["proposed"]))
        # a hand-built over-step proposal (0.97, +0.07) is rejected by the gate
        pid = _inject("ar_collection_rate", 0.97)
        res = gate_mod.approve(pid, approver="Treasurer")
        self.assertFalse(res["ok"])
        self.assertTrue(any("max_step" in r for r in res["reasons"]))

    # 3) A parameter not in the registry cannot be changed.
    def test_frozen_parameter_cannot_change(self):
        with self.assertRaises(registry.FrozenParameterError):
            proposer.propose("tax_rate", AR_OUTCOMES_092)
        with self.assertRaises(registry.FrozenParameterError):
            registry.set_champion("tax_rate", 0.5, by="x", reason="x", ts=None)
        self.assertNotIn("tax_rate", registry.load_store()["champions"])

    # 4) A challenger that regresses the eval set is rejected even if a human approves.
    def test_eval_regression_rejected_despite_human(self):
        pid = _inject("materiality_usd_threshold", 25000.0)  # in bounds, in step
        res = gate_mod.approve(pid, approver="Controller")
        self.assertFalse(res["ok"])
        self.assertTrue(any("eval regression" in r for r in res["reasons"]))
        self.assertEqual(registry.champion_value("materiality_usd_threshold"), 20000.0)

    # 5) Nothing is adopted without explicit approval (propose-only by default).
    def test_propose_only_no_auto_adopt(self):
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        self.assertEqual(p["status"], "pending")
        self.assertEqual(registry.champion_value("ar_collection_rate"), 0.90)  # unchanged
        res = gate_mod.maybe_auto_adopt(p["id"])
        self.assertFalse(res["ok"])
        self.assertEqual(registry.champion_value("ar_collection_rate"), 0.90)  # still unchanged

    # 6) Rollback restores the exact prior value.
    def test_rollback_restores_exact_value(self):
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        gate_mod.approve(p["id"], approver="Treasurer")
        self.assertEqual(registry.champion_value("ar_collection_rate"), 0.92)
        rollback_mod.rollback("ar_collection_rate", 1, by="Treasurer")
        self.assertEqual(registry.champion_value("ar_collection_rate"), 0.90)

    # 7) Every proposal and decision appears in the audit trail.
    def test_audit_trail_records_everything(self):
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        gate_mod.approve(p["id"], approver="Treasurer")
        rollback_mod.rollback("ar_collection_rate", 1, by="Treasurer")
        actions = [e["action"] for e in audit.read_all()]
        self.assertIn("proposed", actions)
        self.assertIn("approved", actions)
        self.assertIn("rollback", actions)
        # the specific proposal id is traceable
        self.assertTrue(audit.entries_for(p["id"]))

    # 8) Tamper test: editing eval ground-truth or a frozen parameter through this
    #    system fails, and the eval ground-truth file is never touched.
    def test_tamper_fails(self):
        # cannot target an eval-truth key (it is not a registry parameter)
        with self.assertRaises(registry.FrozenParameterError):
            proposer.propose("operating_income_2026_05_usd", AR_OUTCOMES_092)
        with self.assertRaises(registry.FrozenParameterError):
            registry.set_champion("net_income_usd", -1, by="x", reason="x", ts=None)
        # run a full legitimate cycle, then confirm eval_set.py is byte-for-byte unchanged
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        gate_mod.approve(p["id"], approver="Treasurer")
        self.assertEqual(_file_hash(EVAL_SET), self.eval_hash_before)

    # 9) Cooldown is respected (bonus).
    def test_cooldown_respected(self):
        p = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        gate_mod.approve(p["id"], approver="Treasurer")  # promoted this cycle
        blocked = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        self.assertEqual(blocked["status"], "blocked_cooldown")
        registry.bump_cycle()  # advance one calibration cycle
        ok = proposer.propose("ar_collection_rate", AR_OUTCOMES_092)
        self.assertEqual(ok["status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
