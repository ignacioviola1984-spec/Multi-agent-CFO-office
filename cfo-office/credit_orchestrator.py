"""
credit_orchestrator.py - The CFO of the credit (LendingClub) office.

Runs the lending close as a staged operating model (credit_stages) with two-tier
HITL: each function signed off by its domain expert (first line), then the CFO
gives the final consolidated sign-off and the board narrative. Numbers come from
credit_core (deterministic); the model only narrates. Persists to cfo_state.json.

Correr:  python credit_orchestrator.py   (needs ANTHROPIC_API_KEY in ../.env)
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # credit_core
sys.path.insert(0, HERE)                                  # shared_state, review

import credit_core as cc
from shared_state import CFOContext
import review
import credit_stages

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"


def agent(system, prompt, max_tokens=700):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# --- deterministic cross-checks between the credit agents -------------------

def cross_checks(ctx):
    """Storage-integrity reconciliation: each analytics agent must have STORED
    exactly what the deterministic engine returns. We re-compute from credit_core
    and compare against the value the agent left in shared state — this catches an
    agent that mutated or mis-stored a figure (real assurance, not a tautology over
    two reads of the same value)."""
    pm, ue = cc.portfolio_metrics(), cc.unit_economics()
    expected = [
        ("Loan Portfolio", "originations_usd", pm["originations_usd"]),
        ("Loan Portfolio", "n_loans", float(pm["n_loans"])),
        ("Revenue & Unit Economics", "interest_income_usd", ue["interest_income_usd"]),
    ]
    issues, compared = [], 0
    for fn, key, engine_val in expected:
        stored = ctx.get(fn, key)
        if stored is None:
            issues.append(f"{fn}.{key} missing from shared state")
            continue
        compared += 1
        if abs(float(stored) - engine_val) > 1:
            issues.append(f"{fn}.{key} stored {float(stored):,.0f} != engine {engine_val:,.0f}")
    ctx.audit("cross_check", "info", f"{compared} shared number(s) reconciled to the engine")
    return issues


def gather_escalations(ctx):
    esc = []
    for f in review.CREDIT_FUNCTIONS:
        esc.extend(tuple(e) for e in ctx.get(f, "escalations", []))
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    return sorted(esc, key=lambda e: order.get(e[0], 9))


def cfo_final_gate(ctx, esc):
    """The CFO's final sign-off — contingent on every function being signed off by
    its domain expert (the first line)."""
    fl = review.first_line_status(ctx, review.CREDIT_FUNCTIONS)
    print(f"\n  [CFO final sign-off] First line: {len(fl['approved'])}/{fl['total']} "
          "credit functions signed off by their domain experts.")
    if not fl["all_approved"]:
        print("   NOT cleared (must resolve before the CFO can sign): " + ", ".join(fl["rejected"]))
        return False
    serious = [e for e in esc if e[0] in ("HIGH", "CRITICAL")]
    if serious:
        print("   Material items for the CFO:")
        for sev, msg in serious:
            print(f"     - [{sev}] {msg}")
    if review._auto():
        return True
    try:
        return input("  CFO — approve the consolidated credit board pack? [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


def compose_cfo_narrative(ctx):
    """The CFO Narrative Agent: translate the analysis into executive commentary."""
    p = ctx.get("Loan Portfolio")
    r = ctx.get("Credit Risk")
    u = ctx.get("Revenue & Unit Economics")
    real = cc.ingestion_summary()["is_real_data"]
    caveat = "" if real else "These figures are on the seeded SAMPLE, not real LendingClub data yet. "
    facts = (
        f"Originations: USD {p.get('originations_usd', 0):,.0f} across {p.get('n_loans', 0)} loans; "
        f"weighted-avg rate {p.get('wair', 0) * 100:.1f}%.\n"
        f"Credit risk: charge-off rate {r.get('charge_off_rate', 0) * 100:.1f}%, "
        f"expected loss USD {r.get('expected_loss_usd', 0):,.0f} "
        f"({r.get('expected_loss_pct', 0) * 100:.1f}% of on-book), "
        f"delinquency {r.get('delinquency_rate', 0) * 100:.1f}%.\n"
        f"Unit economics: interest income USD {u.get('interest_income_usd', 0):,.0f}, "
        f"realized yield {u.get('yield_realized', 0) * 100:.1f}%, take rate {u.get('take_rate', 0) * 100:.2f}%.\n"
        f"Risk narrative: {ctx.get('Credit Risk', 'narrative', '')}\n"
        f"Benchmark: {ctx.get('Public Benchmark', 'narrative', '')}\n"
        f"Model risk: {ctx.get('Model Risk', 'narrative', '')}\n"
        f"DISCLOSURE: {caveat}Expected loss/provisions, take rate and yield are documented modeled "
        f"PROXIES (PD x LGD and a grade-based fee proxy), not booked GAAP figures; the benchmark is "
        f"vs placeholder filing values until the real 10-K/10-Q numbers are loaded."
    )
    return agent(
        "You are the CFO of a consumer-lending fintech. Write a board narrative of 6-8 sentences "
        "covering growth (originations), credit quality (charge-off, expected loss/provisions), unit "
        "economics (yield, take rate) and the outlook. CFO tone, direct, no filler. Use ONLY the "
        "numbers given; do not invent figures. You MUST explicitly state that the expected-loss/"
        "provision and unit-economics figures are documented modeled proxies (and sample-derived if "
        "the disclosure says so), not booked or audited results. Write in English.",
        facts,
    )


def compose_actions(ctx):
    esc = gather_escalations(ctx)
    esc_txt = "\n".join(f"  - [{s}] {m}" for s, m in esc) or "  (no escalations)"
    return agent(
        "You are the CFO of a lending fintech. Propose 3 concrete, prioritized actions from the "
        "escalations. One line each. Do not add new numbers. Write in English.",
        f"Escalations:\n{esc_txt}\n\nPropose 3 prioritized actions.",
    )


def run():
    print("=" * 60)
    print("CREDIT CFO OFFICE | LendingClub portfolio")
    print("=" * 60)
    ctx = CFOContext()
    ctx.audit("Credit CFO", "start", "running the credit operating model")

    stage_results, all_passed = credit_stages.run_all(ctx)
    if not all_passed:
        blocked = next((s for s in stage_results if s["status"] == "blocked"), None)
        ctx.put("Credit CFO", {"status": "blocked_stage",
                               "blocked_stage": blocked["name"] if blocked else None})
        ctx.save()
        print(f"\n  Credit model halted at stage {blocked['id']} ({blocked['name']}): {blocked['reason']}"
              if blocked else "\n  Credit model halted.")
        return ctx

    issues = cross_checks(ctx)
    if issues:
        for i in issues:
            ctx.audit("cross_check", "FAIL", i)
        ctx.put("Credit CFO", {"status": "halted_inconsistent"})
        ctx.save()
        print("\n  Stopped: the credit agents don't agree on the numbers.")
        return ctx
    ctx.audit("cross_check", "ok", "credit agents consistent on the shared numbers")

    ctx.put("Credit CFO", {"first_line": review.first_line_status(ctx, review.CREDIT_FUNCTIONS)})
    esc = gather_escalations(ctx)
    for sev, msg in esc:
        ctx.audit("escalation", sev, msg)

    if not cfo_final_gate(ctx, esc):
        ctx.put("Credit CFO", {"status": "rejected"})
        ctx.audit("Credit CFO", "REJECTED", "CFO did not approve")
        ctx.save()
        print("\n  Stopped by CFO decision.")
        return ctx
    ctx.audit("Credit CFO", "approved", "CFO signed off the consolidated credit board pack")

    narrative = compose_cfo_narrative(ctx)
    actions = compose_actions(ctx)
    ctx.put("CFO Narrative", {"narrative": narrative, "actions": actions, "status": "approved"})
    ctx.audit("CFO Narrative", "ok", "consolidated credit board narrative and actions fixed")

    path = ctx.save()
    print("\n--- CREDIT BOARD NARRATIVE (CFO) ---\n" + narrative)
    print("\n--- PROPOSED ACTIONS ---\n" + actions)
    print(f"\nShared state saved to: {os.path.basename(path)} ({len(ctx.state['audit'])} audit events)")
    return ctx


if __name__ == "__main__":
    run()
