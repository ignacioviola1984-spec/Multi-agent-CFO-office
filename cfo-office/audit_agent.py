"""
audit_agent.py - Audit Agent (independent assurance, third line of defense).

Sits OUTSIDE the functions it audits (reports to the CFO / audit committee, not
to Accounting), which is what makes it independent. It re-performs the close
reconciliations, re-foots the financial statements, re-checks the equity
articulation and that the cash flow ties, and vouches high-value disbursements —
then issues an OPINION (unqualified / qualified / adverse).

Distinct from Internal Controls (which designs and runs the control tests) and
from Accounting & Close (which owns the reconciliations): Audit independently
RE-PERFORMS and ATTESTS. Its own escalation is the opinion qualification, not a
re-raise of items another agent already owns. Numbers/verdicts by code
(finance_core); the model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python audit_agent.py
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # finance_core
sys.path.insert(0, HERE)                                  # shared_state

import finance_core as fc
from shared_state import CFOContext

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIOD = "2026-05"


def agent(system, prompt, max_tokens=400):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def audit_escalations(res):
    """La salida propia de Auditoria es la OPINION (no re-escala las partidas del
    cierre: eso tiene dueno). Solo escala si la opinion no es limpia."""
    if res["opinion"] == "unqualified":
        return []
    sev = "HIGH" if res["opinion"] == "qualified" else "CRITICAL"
    return [[sev, f"audit opinion: {res['opinion'].upper()} - {res['n_exceptions']} "
                  f"procedure(s) failed independent re-performance"]]


def run(ctx=None):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Audit", "start", f"independent re-performance {PERIOD}")

    res = fc.audit_procedures(PERIOD)
    esc = audit_escalations(res)
    register = "\n".join(
        f"[{'OK' if f['ok'] else 'EXCEPTION'}] {f['proc']} ({f['detail']})" for f in res["findings"]
    )
    facts = (
        f"Independent audit procedures for {PERIOD} "
        f"({res['n_procedures']} performed, {res['n_exceptions']} exception(s)):\n{register}\n"
        f"Opinion: {res['opinion'].upper()}."
    )
    narrative = agent(
        "You are Audit (independent assurance). In 2-3 sentences, state the audit opinion and what "
        "you independently re-performed (subledger ties, balance foots, equity articulation, cash "
        "flow ties, high-value disbursements vouched). Use only the register given; do not invent "
        "figures and do not restate business risks owned by other functions. Write in English.",
        facts,
    )

    ctx.put("Audit", {
        "opinion": res["opinion"], "findings": res["findings"],
        "n_procedures": res["n_procedures"], "n_exceptions": res["n_exceptions"],
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Audit", "ok", f"opinion {res['opinion']}; "
              f"{res['n_procedures']} procedures, {res['n_exceptions']} exception(s); "
              f"{len(esc)} escalation(s)")

    if own:
        print("\n--- AUDIT ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
