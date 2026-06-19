"""
cli.py - Operate the bounded self-improvement loop from the command line.

  propose <param> --outcomes FILE [--by NAME]   compute a candidate (deterministic)
  show <id>                                     diff old vs proposed + evidence
  approve <id> --by NAME                         human approval (all gates re-run)
  reject <id> --by NAME --reason TEXT
  rollback <param> <version> --by NAME           restore a prior champion
  status                                         registry, champions, pending proposals

This is propose-only by default: `propose` never changes anything; only `approve`
can promote, and only if every gate passes.
"""

import argparse
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


def _load_outcomes(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Accept either a bare list or {"outcomes": [...]} (demo files use the latter).
    return data["outcomes"] if isinstance(data, dict) else data


def cmd_propose(args):
    outcomes = _load_outcomes(args.outcomes)
    p = proposer.propose(args.param, outcomes, by=args.by)
    print(f"Proposal {p['id']} [{p['status']}]: {p['param']} {p['old']} -> {p['proposed']}")
    print(f"  rationale: {p['rationale']}")


def cmd_show(args):
    p = proposer.get_proposal(args.id)
    if not p:
        print(f"no proposal {args.id}")
        return 1
    print(json.dumps({k: p[k] for k in p if k != "outcomes"}, indent=2, ensure_ascii=False))


def cmd_approve(args):
    res = gate_mod.approve(args.id, approver=args.by)
    if res["ok"]:
        print(f"APPROVED {args.id}: champion updated to v{res['version']}. "
              f"Eval {res['verdict']['eval_result']}.")
    else:
        print(f"NOT ADOPTED {args.id}: {res['reasons']}")
        return 1


def cmd_reject(args):
    gate_mod.reject(args.id, by=args.by, reason=args.reason)
    print(f"rejected {args.id}")


def cmd_rollback(args):
    res = rollback_mod.rollback(args.param, args.version, by=args.by)
    print(res if not res["ok"] else
          f"rolled back {args.param} to v{args.version} (value {res['value']}, now v{res['new_version']})")


def cmd_status(args):
    registry.ensure_init()
    print("REGISTRY (the only changeable parameters):")
    for n in registry.param_names():
        m = registry.REGISTRY[n]
        cur = registry.champion_value(n)
        ver = registry.champion_version(n)
        print(f"  {n:26} champion={cur} (v{ver})  bounds=[{m['min']},{m['max']}] "
              f"step={m['max_step']} cooldown={m['cooldown']} owner={m['owner']}")
    pend = [p for p in proposer.load_proposals()["items"] if p["status"] == "pending"]
    print(f"\nPending proposals: {len(pend)}")
    for p in pend:
        print(f"  {p['id']}: {p['param']} {p['old']} -> {p['proposed']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bounded self-improvement CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("propose"); sp.add_argument("param"); sp.add_argument("--outcomes", required=True); sp.add_argument("--by", default="proposer"); sp.set_defaults(fn=cmd_propose)
    sp = sub.add_parser("show"); sp.add_argument("id"); sp.set_defaults(fn=cmd_show)
    sp = sub.add_parser("approve"); sp.add_argument("id"); sp.add_argument("--by", required=True); sp.set_defaults(fn=cmd_approve)
    sp = sub.add_parser("reject"); sp.add_argument("id"); sp.add_argument("--by", required=True); sp.add_argument("--reason", required=True); sp.set_defaults(fn=cmd_reject)
    sp = sub.add_parser("rollback"); sp.add_argument("param"); sp.add_argument("version", type=int); sp.add_argument("--by", required=True); sp.set_defaults(fn=cmd_rollback)
    sp = sub.add_parser("status"); sp.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
