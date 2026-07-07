"""
ar_agent.py - Accounts Receivable Agent (under Administration).

Owns receivables/collections: open balance, overdue, DSO. Raises the
collections flag (ownership moved here from the Controller, so the risk has a
single owner). Numbers by code (finance_core); the model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python ar_agent.py
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

# Secrets are surfaced through the SecretsProvider (config/secrets.py): load the
# repo-root .env via the provider, so the Anthropic API key is retrieved through
# the secrets interface rather than a scattered os.environ / load_dotenv call.
sys.path.insert(0, os.path.abspath(ROOT))
from config import secrets as appsecrets  # noqa: E402
appsecrets.load_env(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIOD = "2026-05"
AR_OVERDUE_PCT_MAX = 50.0   # por encima, riesgo de cobranza


def agent(system, prompt, max_tokens=350):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


def ar_escalations(m):
    """Flags de AR. La cartera vencida la escala AR (no el Controller)."""
    out = []
    if m["overdue_pct"] > AR_OVERDUE_PCT_MAX:
        out.append(["HIGH", f"{m['overdue_pct']:.0f}% of receivables overdue "
                            f"({_money(m['overdue'])}, {m['n_overdue']} invoices): collections risk"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Accounts Receivable", "start", f"receivables and DSO {period}")

    m = fc.ar_metrics(period)
    esc = ar_escalations(m)
    facts = (
        f"Accounts receivable {period} (USD): total open {_money(m['total'])}, "
        f"current {_money(m['current'])}, overdue {_money(m['overdue'])} ({m['overdue_pct']:.0f}%), "
        f"DSO {m['dso']:.0f} days, {m['n_overdue']} overdue invoices."
    )
    narrative = agent(
        "You are the Accounts Receivable lead. In 2 sentences, explain the collections "
        "situation and its implication for cash. Use only the numbers given; do not invent "
        "figures. Write in English.",
        facts,
    )

    ctx.put("Accounts Receivable", {"metrics": m, "narrative": narrative, "escalations": esc})
    ctx.audit("Accounts Receivable", "ok",
              f"overdue {m['overdue_pct']:.0f}%, DSO {m['dso']:.0f}d; {len(esc)} escalation(s)")

    if own:
        print("\n--- ACCOUNTS RECEIVABLE ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
