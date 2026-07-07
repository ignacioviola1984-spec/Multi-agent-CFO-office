"""
accounting_close_agent.py - Accounting & Close Agent (under Accounting & Reporting).

Executes the month-end close (the engine room the Controller only reviews):
reconciles the AR/AP subledgers to the GL control accounts and verifies the
equity roll-forward (retained earnings move = net income). Raises a flag only if
something does NOT reconcile (an open close item). Numbers by code
(finance_core); the model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python accounting_close_agent.py
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


def agent(system, prompt, max_tokens=400):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


def close_escalations(cr):
    """Flags del cierre: una por cada partida que no concilia. En libros bien
    cerrados no hay ninguna (el valor es PROBAR que ata, no generar ruido)."""
    out = []
    for r in cr["recs"]:
        if r["status"] == "OPEN ITEM":
            out.append(["HIGH", f"{r['item']} subledger does not tie to GL: "
                                f"difference {_money(r['diff'])} (open close item)"])
    if cr["articulation"]["status"] == "BREAK":
        out.append(["HIGH", "retained earnings do not roll forward by net income "
                            f"(off by {_money(cr['articulation']['diff'])})"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Accounting & Close", "start", f"reconciliations and roll-forward {period}")

    cr = fc.close_reconciliations(period)
    esc = close_escalations(cr)
    art = cr["articulation"]
    recs_txt = "; ".join(
        f"{r['item']}: subledger {_money(r['subledger'])} vs GL {_money(r['gl'])} -> {r['status']}"
        for r in cr["recs"]
    )
    facts = (
        f"Close {period} reconciliations:\n{recs_txt}.\n"
        f"Retained earnings roll-forward: movement vs net income -> {art['status']} "
        f"(net income {_money(art['net_income'])}).\n"
        f"Open items: {cr['n_open_items']}."
    )
    narrative = agent(
        "You are the Accounting & Close lead. In 2-3 sentences, state whether the close is clean: "
        "confirm the AR/AP subledgers tie to the GL control accounts and that retained earnings "
        "roll forward by net income, and name any open item. Use only the numbers given; do not "
        "invent figures. Write in English.",
        facts,
    )

    ctx.put("Accounting & Close", {"reconciliations": cr, "narrative": narrative, "escalations": esc})
    ctx.audit("Accounting & Close", "ok",
              f"{'all reconciled' if cr['all_reconciled'] else str(cr['n_open_items']) + ' open item(s)'}; "
              f"{len(esc)} escalation(s)")

    if own:
        print("\n--- ACCOUNTING & CLOSE ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
