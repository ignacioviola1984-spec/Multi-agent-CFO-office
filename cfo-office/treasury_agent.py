"""
treasury_agent.py - Treasury Agent del CFO office.

Mide la liquidez: caja consolidada, burn operativo mensual y runway. Levanta
flags si el runway es ajustado, y deja todo en el estado compartido.

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

load_dotenv(os.path.join(ROOT, ".env"))
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
    cash = fc.cash_total_usd()
    op_income = fc.pnl_usd(period)["operating_income"]
    burn = -op_income if op_income < 0 else 0.0
    runway = (cash / burn) if burn > 0 else None   # None = sin burn (operativo positivo)
    return {"cash": cash, "burn": burn, "runway": runway}


def treasury_escalations(t):
    """Flags de Tesoreria por severidad. Lista de [sev, mensaje]."""
    out = []
    r = t["runway"]
    if r is None:
        return out
    if r < RUNWAY_CRITICA:
        out.append(["CRITICA", f"runway {r:.1f} meses (< {RUNWAY_CRITICA}): riesgo de liquidez"])
    elif r < RUNWAY_ALTA:
        out.append(["ALTA", f"runway {r:.1f} meses (< {RUNWAY_ALTA}): margen de maniobra ajustado"])
    return out


# --- Orquestacion del agente -------------------------------------------

def run(ctx=None):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Treasury", "inicio", f"liquidez y runway {PERIOD}")

    t = compute_treasury(PERIOD)
    esc = treasury_escalations(t)
    runway_txt = f"{t['runway']:.1f} meses" if t["runway"] is not None else "sin burn (operativo positivo)"

    facts = (
        f"Caja consolidada: {_money(t['cash'])}. Burn operativo mensual: {_money(t['burn'])}. "
        f"Runway calculado: {runway_txt}."
    )
    narrative = agent(
        "Sos Tesoreria. Explicas en 2 frases la situacion de runway y su implicancia. "
        "Usas exactamente el runway que te dan; no lo recalcules.",
        facts,
    )

    ctx.put("Treasury", {
        "cash": t["cash"], "burn": t["burn"], "runway": t["runway"],
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Treasury", "ok", f"runway {runway_txt}; {len(esc)} escalamiento(s)")

    if own:
        print("\n--- TREASURY ---\n" + narrative)
        path = ctx.save()
        print(f"\nEstado compartido guardado en: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
