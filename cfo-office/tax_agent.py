"""
tax_agent.py - Tax Agent (under Administration).

Owns tax/compliance exposure: pending obligations, overdue, upcoming (<=30d),
by jurisdiction. Flags overdue tax (penalty/compliance risk). Numbers by code
(finance_core); the model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python tax_agent.py
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


def agent(system, prompt, max_tokens=350):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


def tax_escalations(m):
    """Tax vencido = riesgo de multas/compliance. Lo escala Tax."""
    out = []
    if m["overdue"] > 0:
        out.append(["HIGH", f"{_money(m['overdue'])} in overdue tax obligations across "
                            f"{len(m['by_jurisdiction'])} jurisdictions: compliance/penalty risk"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Tax", "start", f"tax obligations and compliance {period}")

    # tax obligations are a pending-balance snapshot (no per-period subledger), so
    # the period is used for the reporting label; the figures are as-of the close.
    m = fc.tax_metrics()
    esc = tax_escalations(m)
    facts = (
        f"Tax obligations {period} (USD): pending {_money(m['pending_total'])}, "
        f"overdue {_money(m['overdue'])}, due within 30 days {_money(m['upcoming_30d'])}, "
        f"across {len(m['by_jurisdiction'])} jurisdictions."
    )
    narrative = agent(
        "You are the Tax lead. In 2 sentences, explain the tax/compliance exposure "
        "(overdue and upcoming obligations) and its implication. Use only the numbers given; "
        "do not invent figures. Write in English.",
        facts,
    )

    ctx.put("Tax", {"metrics": m, "narrative": narrative, "escalations": esc})
    ctx.audit("Tax", "ok",
              f"overdue {_money(m['overdue'])}, pending {_money(m['pending_total'])}; {len(esc)} escalation(s)")

    if own:
        print("\n--- TAX ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
