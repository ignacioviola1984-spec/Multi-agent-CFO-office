"""
treasury_agent.py - Treasury Agent del CFO office.

Mide la liquidez: caja consolidada, burn operativo mensual, runway, y un
forecast directo de caja a 13 semanas (cobranzas AR, pagos AP/tax y burn
recurrente). Levanta flags si el runway es ajustado o si la caja se vuelve
negativa dentro del horizonte. Deja todo en el estado compartido.

Numeros por codigo (deterministicos, reusa finance_core). El modelo razona y
redacta; nunca inventa una cifra. El runway lo calcula el codigo; el modelo lo
usa, no lo recalcula.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python treasury_agent.py
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
RUNWAY_CRITICA = 6     # meses: por debajo, riesgo de liquidez
RUNWAY_ALTA = 12       # meses: por debajo, margen de maniobra ajustado


def agent(system, prompt, max_tokens=400):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


# --- Capa deterministica (numeros por codigo) ---------------------------

def compute_treasury(period):
    cash = fc.cash_total_usd(period)
    op_income = fc.pnl_usd(period)["operating_income"]
    burn = -op_income if op_income < 0 else 0.0
    runway = (cash / burn) if burn > 0 else None   # None = sin burn (operativo positivo)
    return {"cash": cash, "burn": burn, "runway": runway,
            "forecast": fc.cash_forecast_13w()}


def treasury_escalations(t):
    """Flags de Tesoreria por severidad. Lista de [sev, mensaje]."""
    out = []
    r = t["runway"]
    if r is not None and r < RUNWAY_CRITICA:
        out.append(["CRITICAL", f"runway {r:.1f} months (< {RUNWAY_CRITICA}): liquidity risk"])
    elif r is not None and r < RUNWAY_ALTA:
        out.append(["HIGH", f"runway {r:.1f} months (< {RUNWAY_ALTA}): tight room to maneuver"])
    # 13-week forecast: la caja se vuelve negativa dentro del horizonte (granular,
    # distinto del runway mensual): riesgo critico de liquidez de corto plazo.
    wn = t["forecast"]["week_cash_negative"]
    if wn is not None:
        out.append(["CRITICAL", f"cash turns negative in week {wn} of the 13-week forecast"])
    return out


# --- Orquestacion del agente -------------------------------------------

def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Treasury", "start", f"liquidity and runway {period}")

    t = compute_treasury(period)
    esc = treasury_escalations(t)
    runway_txt = f"{t['runway']:.1f} months" if t["runway"] is not None else "no burn (operating positive)"
    f = t["forecast"]
    fcast_txt = (
        f"13-week cash forecast: ending cash {_money(f['ending_cash'])}, "
        f"trough {_money(f['min_cash'])} in week {f['min_week']}"
        + (f"; CASH TURNS NEGATIVE in week {f['week_cash_negative']}." if f["week_cash_negative"]
           else "; stays positive across the horizon.")
    )

    facts = (
        f"Consolidated cash: {_money(t['cash'])}. Monthly operating burn: {_money(t['burn'])}. "
        f"Computed runway: {runway_txt}.\n{fcast_txt}"
    )
    narrative = agent(
        "You are Treasury. In 2-3 sentences, explain the liquidity situation: the runway and what "
        "the 13-week cash forecast shows (ending cash, the trough week, and whether cash stays "
        "positive). Use exactly the figures given; do not recompute. Write in English.",
        facts,
    )

    ctx.put("Treasury", {
        "cash": t["cash"], "burn": t["burn"], "runway": t["runway"], "forecast": f,
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Treasury", "ok",
              f"runway {runway_txt}; 13w ending {_money(f['ending_cash'])}; {len(esc)} escalation(s)")

    if own:
        print("\n--- TREASURY ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
