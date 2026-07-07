"""
controller_agent.py - Controller Agent del CFO office.

Revisa el cierre del periodo: valida la consistencia interna del P&L, calcula
margenes y la cartera por cobrar, levanta flags de riesgo, y deja todo en el
estado compartido para que el CFO orquestador lo consuma.

Numeros por codigo (deterministicos, reusa finance_core). El modelo razona y
redacta; nunca inventa una cifra.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python controller_agent.py
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


def agent(system, prompt, max_tokens=500):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


# --- Capa deterministica (numeros por codigo) ---------------------------

def check_pnl(p):
    """Consistencia interna del P&L (mismos invariantes que el operating model)."""
    issues = []
    if p["revenue"] <= 0:
        issues.append("revenue not positive")
    if p["gross"] > p["revenue"]:
        issues.append("gross > revenue (impossible)")
    if p["opex"] < 0:
        issues.append("opex negative")
    return issues


def compute_close(period):
    pnl = fc.pnl_usd(period)
    ar = fc.ar_overdue_usd()
    gm = (pnl["gross"] / pnl["revenue"] * 100) if pnl["revenue"] else 0.0
    om = (pnl["operating_income"] / pnl["revenue"] * 100) if pnl["revenue"] else 0.0
    return {"pnl": pnl, "ar": ar, "gross_margin_pct": gm,
            "op_margin_pct": om, "issues": check_pnl(pnl)}


def close_escalations(close):
    """Flags del Controller, por severidad. Lista de [sev, mensaje]."""
    out = []
    if close["issues"]:
        out.append(["CRITICAL", "P&L inconsistent: " + "; ".join(close["issues"])])
    if close["pnl"]["operating_income"] < 0:
        out.append(["HIGH", "operating loss: cost structure needs review"])
    # Overdue receivables are owned and escalated by the Accounts Receivable agent
    # (under Administration), so the risk has a single owner — not double-counted here.
    return out


# --- Orquestacion del agente -------------------------------------------

def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Controller", "start", f"close review {period}")

    close = compute_close(period)
    esc = close_escalations(close)
    pnl = close["pnl"]

    facts = (
        f"Close {period} (USD): revenue {_money(pnl['revenue'])}, "
        f"gross {_money(pnl['gross'])} ({close['gross_margin_pct']:.1f}%), "
        f"opex {_money(pnl['opex'])}, operating income {_money(pnl['operating_income'])} "
        f"({close['op_margin_pct']:.1f}%).\n"
        f"Accounts receivable (USD): current {_money(close['ar']['current'])}, "
        f"overdue {_money(close['ar']['overdue'])} ({close['ar']['overdue_pct']:.0f}% of total)."
    )
    narrative = agent(
        "You are the Controller. Summarize the close in 2 sentences and list at most 3 "
        "concrete risk flags. Use only the numbers given; do not invent figures. Write in English.",
        facts,
    )

    ctx.put("Controller", {
        "pnl": pnl, "ar": close["ar"],
        "gross_margin_pct": close["gross_margin_pct"],
        "op_margin_pct": close["op_margin_pct"],
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Controller", "ok", f"close reviewed; {len(esc)} escalation(s)")

    if own:
        print("\n--- CONTROLLER ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
