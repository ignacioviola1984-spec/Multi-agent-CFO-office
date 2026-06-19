"""
gate.py - The promotion gate (champion vs challenger).

A challenger becomes the champion ONLY if ALL of these hold:
  (a) within bounds, step cap and cooldown (registry);
  (b) the deterministic eval suite passes with NO regression vs the current
      champion (the parameters can only move deterministic numbers, so this is
      the binding check; the LLM eval suites are unaffected by construction);
  (c) a backtest over the outcomes window shows the metric does not get worse;
  (d) a human (the registry owner / a checker, not the proposer) approves.

Default posture is PROPOSE-ONLY: nothing is adopted automatically. Auto-adopt is
disabled behind AUTO_ADOPT_ENABLED, which is off by default.

On approval the new champion is written, the cycle advances, and the FULL
decision (old, new, evidence, eval result, approver, timestamp) is appended to
the append-only audit trail. Every prior champion stays in the version history,
so any change is reversible.
"""

import contextlib
import importlib
import io
import os
import sys

import registry
import audit
import propose as proposer

# Off by default. Promotion requires an explicit human approval; nothing is
# adopted automatically unless this is deliberately turned on.
AUTO_ADOPT_ENABLED = False

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _wire_paths():
    for p in (os.path.join(_ROOT, "orchestration"), os.path.join(_ROOT, "evals")):
        ap = os.path.abspath(p)
        if ap not in sys.path:
            sys.path.insert(0, ap)


def _reload_fc():
    """Reload finance_core so it re-reads the champion store from disk."""
    _wire_paths()
    import finance_core
    importlib.reload(finance_core)
    return finance_core


def _run_numbers_suite():
    """Run the deterministic Numbers eval suite; return (passed, total)."""
    _reload_fc()
    import eval_runner
    importlib.reload(eval_runner)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        passed, total = eval_runner.suite_numbers()
    return passed, total


def evals_current(sd=None):
    """Eval result with the champion store exactly as it is on disk."""
    sd = registry.state_dir(sd)
    prev = os.environ.get("SELFIMPROVE_STATE_DIR")
    os.environ["SELFIMPROVE_STATE_DIR"] = sd
    try:
        return _run_numbers_suite()
    finally:
        if prev is None:
            os.environ.pop("SELFIMPROVE_STATE_DIR", None)
        else:
            os.environ["SELFIMPROVE_STATE_DIR"] = prev


def evals_with_candidate(param, value, sd=None):
    """Temporarily apply `value` to `param`, run the suite, then fully restore.

    Writes the candidate into the same champions.json that finance_core reads,
    reloads finance_core so the value takes effect, runs the suite, and restores
    the store byte-for-byte. No version is bumped (this is a trial, not a
    promotion).
    """
    sd = registry.state_dir(sd)
    registry.ensure_init(sd)
    path = os.path.join(sd, "champions.json")
    with open(path, encoding="utf-8") as f:
        backup = f.read()
    prev = os.environ.get("SELFIMPROVE_STATE_DIR")
    os.environ["SELFIMPROVE_STATE_DIR"] = sd
    try:
        store = registry.load_store(sd)
        store["champions"][param] = {
            "value": value, "version": store["champions"][param]["version"],
        }
        registry.save_store(store, sd)
        return _run_numbers_suite()
    finally:
        with open(path, "w", encoding="utf-8") as f:
            f.write(backup)
        if prev is None:
            os.environ.pop("SELFIMPROVE_STATE_DIR", None)
        else:
            os.environ["SELFIMPROVE_STATE_DIR"] = prev
        _reload_fc()  # reload finance_core back to the restored store


# --------------------------------------------------------------------------
# Individual gate checks.
# --------------------------------------------------------------------------
def static_checks(param, value, sd=None):
    return {
        "within_bounds": registry.within_bounds(param, value),
        "within_step": registry.within_step(param, value, sd),
        "cooldown_ok": registry.cooldown_ok(param, sd),
    }


def _backtest_metric(param, value, outcomes):
    if param == "ar_collection_rate":
        # Lower aggregate calibration error is better -> return its negative.
        err = sum(abs(float(o["actual_collected"]) - value * float(o["forecast_collectible"]))
                  for o in outcomes)
        return -err
    f1, _, _ = proposer._f1(value, outcomes)
    return f1


def backtest_ok(proposal, sd=None):
    outcomes = proposal.get("outcomes") or []
    if not outcomes:
        return True, {"note": "no outcomes window to backtest"}
    param = proposal["param"]
    m_old = _backtest_metric(param, proposal["old"], outcomes)
    m_new = _backtest_metric(param, proposal["proposed"], outcomes)
    return (m_new >= m_old - 1e-9), {"metric_old": round(m_old, 4), "metric_new": round(m_new, 4)}


def evaluate(proposal, sd=None):
    """Run every non-human gate and return a structured verdict (no side effects)."""
    param, value = proposal["param"], proposal["proposed"]
    checks = static_checks(param, value, sd)
    reasons = []
    if not checks["within_bounds"]:
        m = registry.REGISTRY[param]
        reasons.append(f"out of bounds: {value} not in [{m['min']}, {m['max']}]")
    if not checks["within_step"]:
        reasons.append(f"exceeds max_step {registry.REGISTRY[param]['max_step']} from {proposal['old']}")
    if not checks["cooldown_ok"]:
        reasons.append(f"cooldown not elapsed ({registry.cooldown_remaining(param, sd)} left)")

    eval_result = None
    if not reasons:  # only spend the eval run if the cheap checks pass
        base_p, base_t = evals_current(sd)
        cand_p, cand_t = evals_with_candidate(param, value, sd)
        eval_result = {"baseline": [base_p, base_t], "candidate": [cand_p, cand_t]}
        if not (cand_p == cand_t and cand_p >= base_p):
            reasons.append(f"eval regression: candidate {cand_p}/{cand_t} vs baseline {base_p}/{base_t}")

    bt_ok, bt = backtest_ok(proposal, sd)
    if not bt_ok:
        reasons.append(f"backtest worse: {bt}")

    return {
        "ok": len(reasons) == 0,
        "checks": checks,
        "eval_result": eval_result,
        "backtest": bt,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------
# Decisions (promotion requires a human; all gates must pass regardless).
# --------------------------------------------------------------------------
def _gate_and_promote(prop, approver, sd, human):
    """Run every non-human gate; promote only if ALL pass. Shared by the human
    approval path and the (off-by-default) auto-adopt path so both go through the
    identical bound + eval + backtest checks."""
    pid = prop["id"]
    verdict = evaluate(prop, sd)
    if not verdict["ok"]:
        proposer.update_proposal(pid, sd, status="rejected", decided_by=approver,
                                 reasons=verdict["reasons"], gate=verdict)
        audit.record("approval_rejected",
                     f"{prop['param']} {prop['old']}->{prop['proposed']} rejected: {verdict['reasons']}",
                     sd=sd, proposal_id=pid, param=prop["param"], approver=approver,
                     reasons=verdict["reasons"], eval_result=verdict["eval_result"])
        return {"ok": False, "reasons": verdict["reasons"], "verdict": verdict}

    ts = audit.now_iso()
    new_version = registry.set_champion(
        prop["param"], prop["proposed"], by=approver,
        reason=f"approved proposal {pid}", ts=ts, proposal_id=pid,
        evidence=prop["evidence"], kind="promote", sd=sd,
    )
    proposer.update_proposal(pid, sd, status="approved", decided_by=approver,
                             new_version=new_version, gate=verdict, human=human)
    audit.record("approved",
                 f"{prop['param']} {prop['old']}->{prop['proposed']} (v{new_version}) by {approver}"
                 + ("" if human else " [AUTO-ADOPT, no human]"),
                 sd=sd, proposal_id=pid, param=prop["param"], old=prop["old"],
                 new=prop["proposed"], version=new_version, approver=approver, human=human,
                 evidence=prop["evidence"], eval_result=verdict["eval_result"],
                 backtest=verdict["backtest"])
    return {"ok": True, "version": new_version, "verdict": verdict}


def approve(pid, approver, sd=None):
    """Human approval. Necessary but NOT sufficient: every gate must still pass.

    Maker-checker by ROLE: the approver must be the parameter's registered owner
    (registry `owner`) and must not be the proposer. If the challenger regresses
    the evals or breaks any bound, it is rejected even though a human approved it.
    """
    prop = proposer.get_proposal(pid, sd)
    if prop is None:
        return {"ok": False, "reasons": [f"no proposal {pid}"]}
    if prop["status"] != "pending":
        return {"ok": False, "reasons": [f"proposal {pid} is '{prop['status']}', not pending"]}

    owner = registry.REGISTRY[prop["param"]]["owner"]
    if not approver or approver != owner or approver == prop.get("by"):
        if not approver:
            reason = "no human approver"
        elif approver == prop.get("by"):
            reason = "approver must differ from the proposer (maker-checker)"
        else:
            reason = f"approval must come from the parameter owner ('{owner}')"
        proposer.update_proposal(pid, sd, status="rejected", decided_by=approver or None, reasons=[reason])
        audit.record("approval_rejected", f"{prop['param']}: {reason}", sd=sd,
                     proposal_id=pid, param=prop["param"], approver=approver, reasons=[reason])
        return {"ok": False, "reasons": [reason]}

    return _gate_and_promote(prop, approver, sd, human=True)


def reject(pid, by, reason, sd=None):
    prop = proposer.get_proposal(pid, sd)
    if prop is None:
        return {"ok": False, "reasons": [f"no proposal {pid}"]}
    proposer.update_proposal(pid, sd, status="rejected", decided_by=by, reasons=[reason])
    audit.record("rejected", f"{prop['param']}: {reason}", sd=sd,
                 proposal_id=pid, param=prop["param"], by=by, reason=reason)
    return {"ok": True}


def maybe_auto_adopt(pid, sd=None):
    """Auto-adopt path. Refuses unless AUTO_ADOPT_ENABLED is explicitly turned on.

    Even when enabled it is NOT a bypass of safety: it runs the identical bound +
    eval + backtest gate via _gate_and_promote. It only waives the human owner
    approval, and records the adoption as non-human.
    """
    if not AUTO_ADOPT_ENABLED:
        return {"ok": False, "reasons": ["auto-adopt disabled (propose-only by default)"]}
    prop = proposer.get_proposal(pid, sd)
    if prop is None or prop["status"] != "pending":
        return {"ok": False, "reasons": ["no pending proposal"]}
    return _gate_and_promote(prop, approver="auto-adopt (no human)", sd=sd, human=False)
