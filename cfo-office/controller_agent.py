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

load_dotenv(os.path.join(ROOT, ".env"))
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
        issues.append("revenue no positivo")
    if p["gross"] > p["revenue"]:
        issues.append("gross > revenue (imposible)")
    if p["opex"] < 0:
        issues.append("opex negativo")
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
        out.append(["CRITICA", "P&L inconsistente: " + "; ".join(close["issues"])])
    if close["pnl"]["operating_income"] < 0:
        out.append(["ALTA", "perdida operativa: requiere revision de estructura de gasto"])
    if close["ar"]["overdue_pct"] > 50:
        out.append(["ALTA", f"{close['ar']['overdue_pct']:.0f}% de la cartera por cobrar esta vencida"])
    return out


# --- Orquestacion del agente -------------------------------------------

def run(ctx=None):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Controller", "inicio", f"revision de cierre {PERIOD}")

    close = compute_close(PERIOD)
    esc = close_escalations(close)
    pnl = close["pnl"]

    facts = (
        f"Cierre {PERIOD} (USD): revenue {_money(pnl['revenue'])}, "
        f"gross {_money(pnl['gross'])} ({close['gross_margin_pct']:.1f}%), "
        f"opex {_money(pnl['opex'])}, operating income {_money(pnl['operating_income'])} "
        f"({close['op_margin_pct']:.1f}%).\n"
        f"Cuentas por cobrar (USD): corriente {_money(close['ar']['current'])}, "
        f"vencida {_money(close['ar']['overdue'])} ({close['ar']['overdue_pct']:.0f}% del total)."
    )
    narrative = agent(
        "Sos el Controller. Resumis el cierre en 2 frases y listas como maximo 3 flags "
        "de riesgo concretos. Usas solo los numeros dados; no inventes cifras.",
        facts,
    )

    ctx.put("Controller", {
        "pnl": pnl, "ar": close["ar"],
        "gross_margin_pct": close["gross_margin_pct"],
        "op_margin_pct": close["op_margin_pct"],
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Controller", "ok", f"cierre revisado; {len(esc)} escalamiento(s)")

    if own:
        print("\n--- CONTROLLER ---\n" + narrative)
        path = ctx.save()
        print(f"\nEstado compartido guardado en: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
