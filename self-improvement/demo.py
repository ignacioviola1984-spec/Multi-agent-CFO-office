"""
demo.py - End-to-end demonstration of the bounded self-improvement loop.

Runs the loop on demonstration data and shows, in order:
  1) one ACCEPTED proposal (deterministic calibration -> all gates pass ->
     human approval -> champion updated), with the eval pass and the audit entry;
  2) one REJECTED out-of-bounds proposal (the gate refuses it);
  3) a BONUS rejection: an in-bounds change that would regress the evals is
     refused even though a human approves it;
  4) a rollback restoring the prior champion.

Starts from a clean state each run so it is reproducible.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import registry
import propose as proposer
import gate as gate_mod
import rollback as rollback_mod
import audit


def _reset_state():
    sd = registry.state_dir()
    for fn in ("champions.json", "proposals.json", "audit_trail.jsonl"):
        p = os.path.join(sd, fn)
        if os.path.exists(p):
            os.remove(p)
    registry.ensure_init()


def _inject_raw_proposal(param, proposed, by="proposer"):
    """Append a hand-built proposal (e.g. out-of-bounds or eval-breaking) to show
    the gate refusing it. Simulates a malformed/tampered challenger."""
    store = proposer.load_proposals()
    store["seq"] += 1
    pid = f"P{store['seq']}"
    store["items"].append({
        "id": pid, "param": param, "old": registry.champion_value(param),
        "proposed": proposed, "raw_candidate": proposed, "evidence": {"note": "raw injected proposal"},
        "rationale": "(injected for demonstration)", "status": "pending", "by": by, "outcomes": [],
    })
    proposer.save_proposals(store)
    audit.record("proposed", f"{param}: {registry.champion_value(param)} -> {proposed} (injected)",
                 proposal_id=pid, param=param, proposed=proposed)
    return pid


def _hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    _reset_state()

    _hr("REGISTRY - the only values that can ever change")
    for n in registry.param_names():
        m = registry.REGISTRY[n]
        print(f"  {n:26} = {registry.champion_value(n)}  bounds=[{m['min']},{m['max']}] "
              f"step={m['max_step']} owner={m['owner']}")

    # ----------------------------------------------------------------------
    _hr("1) ACCEPTED proposal: ar_collection_rate")
    with open(os.path.join(HERE, "demo_data", "ar_outcomes.json"), encoding="utf-8") as f:
        ar_outcomes = json.load(f)["outcomes"]
    p = proposer.propose("ar_collection_rate", ar_outcomes, by="proposer")
    print(f"Proposal {p['id']}: {p['param']} {p['old']} -> {p['proposed']}")
    print(f"  evidence : {p['evidence']}")
    print(f"  rationale: {p['rationale']}")
    print("\nHuman gate: Treasurer reviews and approves (maker-checker)...")
    res = gate_mod.approve(p["id"], approver="Treasurer")
    print(f"  -> ok={res['ok']}  eval={res['verdict']['eval_result']}  backtest={res['verdict']['backtest']}")
    print(f"  champion now: ar_collection_rate = {registry.champion_value('ar_collection_rate')} "
          f"(v{registry.champion_version('ar_collection_rate')})")

    registry.bump_cycle()  # a new calibration cycle (clears cooldown for the next round)

    # ----------------------------------------------------------------------
    _hr("2) REJECTED proposal: out of bounds")
    pid = _inject_raw_proposal("ar_collection_rate", 1.05)
    print(f"Proposal {pid}: ar_collection_rate {registry.champion_value('ar_collection_rate')} -> 1.05 "
          f"(bounds are [0.80, 0.98])")
    print("Human gate: Treasurer tries to approve...")
    res = gate_mod.approve(pid, approver="Treasurer")
    print(f"  -> ok={res['ok']}  reasons={res['reasons']}")
    print(f"  champion unchanged: ar_collection_rate = {registry.champion_value('ar_collection_rate')}")

    # ----------------------------------------------------------------------
    _hr("3) BONUS - in-bounds change that would REGRESS the evals is refused")
    pid = _inject_raw_proposal("materiality_usd_threshold", 25000.0)
    print(f"Proposal {pid}: materiality_usd_threshold 20000.0 -> 25000.0 "
          f"(in bounds, within step, but drops the material-variance count from 2 to 1)")
    print("Human gate: Controller tries to approve...")
    res = gate_mod.approve(pid, approver="Controller")
    print(f"  -> ok={res['ok']}  reasons={res['reasons']}")
    print(f"  champion unchanged: materiality_usd_threshold = {registry.champion_value('materiality_usd_threshold')}")

    # ----------------------------------------------------------------------
    _hr("4) ROLLBACK - restore the prior champion in one step")
    print(f"Before: ar_collection_rate = {registry.champion_value('ar_collection_rate')}")
    res = rollback_mod.rollback("ar_collection_rate", 1, by="Treasurer")
    print(f"  rollback -> {res}")
    print(f"After : ar_collection_rate = {registry.champion_value('ar_collection_rate')}")

    # ----------------------------------------------------------------------
    _hr("AUDIT TRAIL (append-only)")
    for e in audit.read_all():
        extra = ""
        if e["action"] == "approved":
            extra = f"  [eval {e['eval_result']['candidate']}]"
        print(f"  {e['ts']}  {e['action']:18} {e['detail']}{extra}")


if __name__ == "__main__":
    main()
