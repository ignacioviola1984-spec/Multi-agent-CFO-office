"""
accounting_reporting_agent.py - Accounting & Reporting Agent (supervisor).

A second level of orchestration (like Administration): the CFO delegates the
record-to-report function to Accounting & Reporting, which coordinates two
sub-agents over the same shared state — Accounting & Close (executes the close
and reconciles) and Financial Reporting (produces the three statements) — and
consolidates their flags into a single report for the CFO.

    CFO orchestrator -> Accounting & Reporting -> Accounting & Close / Financial Reporting

Each sub-agent computes its numbers in code (finance_core). The model only
narrates. Order matters: the close runs before reporting (you report off the
closed books).

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python accounting_reporting_agent.py
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # finance_core
sys.path.insert(0, HERE)                                  # shared_state + sub-agentes

from shared_state import CFOContext
import review
import accounting_close_agent
import financial_reporting_agent

# Secrets are surfaced through the SecretsProvider (config/secrets.py): load the
# repo-root .env via the provider, so the Anthropic API key is retrieved through
# the secrets interface rather than a scattered os.environ / load_dotenv call.
sys.path.insert(0, os.path.abspath(ROOT))
from config import secrets as appsecrets  # noqa: E402
appsecrets.load_env(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

SUB_AGENTS = ["Accounting & Close", "Financial Reporting"]


def agent(system, prompt, max_tokens=450):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def run(ctx=None, period="2026-05"):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Accounting & Reporting", "start", "coordinating close and reporting")

    # Close first (signed off by the Accounting Manager), then reporting (signed
    # off by Technical Accounting / Reporting): maker-checker per function.
    accounting_close_agent.run(ctx, period)
    cr = ctx.get("Accounting & Close", "reconciliations", {})
    review.review(ctx, "Accounting & Close",
                  f"close {'reconciled' if cr.get('all_reconciled') else str(cr.get('n_open_items',0))+' open item(s)'}, "
                  f"articulation {cr.get('articulation',{}).get('status','?')}")
    financial_reporting_agent.run(ctx, period)
    bs = ctx.get("Financial Reporting", "balance_sheet", {})
    cf = ctx.get("Financial Reporting", "cash_flow", {})
    review.review(ctx, "Financial Reporting",
                  f"balance foots ({abs(bs.get('balance_check',0))<=1.0}), cash flow ties ({bool(cf.get('foots'))})")

    esc = []
    for a in SUB_AGENTS:
        esc.extend([list(e) for e in ctx.get(a, "escalations", [])])

    bits = "\n".join(f"- {a}: {ctx.get(a, 'narrative', '')}" for a in SUB_AGENTS)
    narrative = agent(
        "You are the Head of Accounting & Reporting. In 2-3 sentences, CFO tone, confirm the close "
        "is clean (subledgers tie, equity articulates) and that the three financial statements were "
        "produced and foot. Use only what's given; do not add new numbers. Write in English.",
        bits,
    )

    ctx.put("Accounting & Reporting", {"narrative": narrative, "escalations": esc, "covers": SUB_AGENTS,
                                       "reviews": {a: ctx.get(a, "review") for a in SUB_AGENTS}})
    ctx.audit("Accounting & Reporting", "ok", f"close + reporting consolidated; {len(esc)} escalation(s)")

    if own:
        print("\n--- ACCOUNTING & REPORTING ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
