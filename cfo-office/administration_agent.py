"""
administration_agent.py - Administration Agent (supervisor).

A second level of orchestration: the CFO delegates the administrative function
to Administration, which coordinates three sub-agents over the same shared
state — Accounts Receivable, Accounts Payable and Tax — consolidates their
flags, and writes a working-capital + compliance summary for the CFO.

    CFO orchestrator -> Administration -> AR / AP / Tax

Each sub-agent computes its numbers in code (finance_core); Administration
rolls up their escalations into a single "Administration" entry so the CFO
sees one report (no double-counting). The model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python administration_agent.py
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
import ar_agent
import ap_agent
import tax_agent

# Secrets are surfaced through the SecretsProvider (config/secrets.py): load the
# repo-root .env via the provider, so the Anthropic API key is retrieved through
# the secrets interface rather than a scattered os.environ / load_dotenv call.
sys.path.insert(0, os.path.abspath(ROOT))
from config import secrets as appsecrets  # noqa: E402
appsecrets.load_env(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

SUB_AGENTS = ["Accounts Receivable", "Accounts Payable", "Tax"]


def agent(system, prompt, max_tokens=500):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def run(ctx=None, period="2026-05"):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Administration", "start", "coordinating AR, AP and Tax")

    # Corre cada sub-agente y lo somete a la firma de SU experto de dominio
    # (maker-checker de primera linea): Collections firma AR, AP firma AP, el
    # Tax Manager firma Tax. No es el Head of Administration el que aprueba el
    # detalle de cada uno: cada funcion la firma quien tiene el conocimiento.
    ar_agent.run(ctx, period)
    ar = ctx.get("Accounts Receivable", "metrics", {})
    review.review(ctx, "Accounts Receivable",
                  f"AR overdue {ar.get('overdue_pct',0):.0f}% (USD {ar.get('overdue',0):,.0f}), DSO {ar.get('dso',0):.0f}d")
    ap_agent.run(ctx, period)
    ap = ctx.get("Accounts Payable", "metrics", {})
    review.review(ctx, "Accounts Payable",
                  f"AP overdue USD {ap.get('overdue',0):,.0f}, DPO {ap.get('dpo',0):.0f}d")
    tax_agent.run(ctx, period)
    tx = ctx.get("Tax", "metrics", {})
    review.review(ctx, "Tax",
                  f"Tax overdue USD {tx.get('overdue',0):,.0f} of USD {tx.get('pending_total',0):,.0f} pending")

    # Consolida sus escalamientos en un unico "Administration" (sin doble conteo:
    # el CFO ve a Administration como un solo reporte, no a AR/AP/Tax por separado).
    esc = []
    for a in SUB_AGENTS:
        esc.extend([list(e) for e in ctx.get(a, "escalations", [])])

    bits = "\n".join(f"- {a}: {ctx.get(a, 'narrative', '')}" for a in SUB_AGENTS)
    narrative = agent(
        "You are the Head of Administration. In 3-4 sentences, CFO tone, summarize working "
        "capital (receivables and payables) and tax/compliance exposure, and the single most "
        "important action. Use only what's given; do not add new numbers. Write in English.",
        bits,
    )

    ctx.put("Administration", {"narrative": narrative, "escalations": esc, "covers": SUB_AGENTS,
                               "reviews": {a: ctx.get(a, "review") for a in SUB_AGENTS}})
    ctx.audit("Administration", "ok", f"AR/AP/Tax consolidated; {len(esc)} escalation(s)")

    if own:
        print("\n--- ADMINISTRATION ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
