"""
ap_agent.py - Accounts Payable Agent (under Administration).

Owns payables: open balance, overdue bills, upcoming (<=30d), DPO. Flags
material overdue payables (paying suppliers late = operational/supplier risk).
Numbers by code (finance_core); the model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python ap_agent.py
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
AP_OVERDUE_MAX = 50000.0   # por encima, riesgo con proveedores


def agent(system, prompt, max_tokens=350):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


def ap_escalations(m):
    out = []
    if m["overdue"] > AP_OVERDUE_MAX:
        out.append(["HIGH", f"{_money(m['overdue'])} in overdue payables "
                            f"({m['n_overdue']} bills, DPO {m['dpo']:.0f}d): supplier/operational risk"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Accounts Payable", "start", f"payables and DPO {period}")

    m = fc.ap_metrics(period)
    esc = ap_escalations(m)
    facts = (
        f"Accounts payable {period} (USD): open {_money(m['open_total'])}, "
        f"overdue {_money(m['overdue'])} ({m['n_overdue']} bills), "
        f"due within 30 days {_money(m['upcoming_30d'])}, DPO {m['dpo']:.0f} days."
    )
    narrative = agent(
        "You are the Accounts Payable lead. In 2 sentences, explain the payables situation "
        "(overdue and upcoming) and its implication for cash and suppliers. Use only the "
        "numbers given; do not invent figures. Write in English.",
        facts,
    )

    ctx.put("Accounts Payable", {"metrics": m, "narrative": narrative, "escalations": esc})
    ctx.audit("Accounts Payable", "ok",
              f"overdue {_money(m['overdue'])}, DPO {m['dpo']:.0f}d; {len(esc)} escalation(s)")

    if own:
        print("\n--- ACCOUNTS PAYABLE ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
