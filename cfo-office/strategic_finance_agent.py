"""
strategic_finance_agent.py - Strategic Finance Agent del CFO office.

Mira el negocio con lente estrategica, no de cierre: calidad del crecimiento,
eficiencia del capital y camino a la rentabilidad. Calcula (por codigo) las
metricas SaaS canonicas y un set de escenarios, levanta flags estrategicos, y
deja todo en el estado compartido para el CFO.

Metricas (todas deterministicas, reusa finance_core):
  - Run-rate (ARR proxy) y crecimiento anualizado del run-rate.
  - Rule of 40 = crecimiento anualizado (%) + margen operativo (%).
  - Burn multiple = burn mensual / revenue nuevo mensual (eficiencia de capital).
  - Magic number = revenue nuevo / S&M del periodo previo (eficiencia comercial).
  - Mix de gasto (COGS/S&M/R&D/G&A como % del revenue).
  - Gap de margen a breakeven (pp) y 3 escenarios de crecimiento.

Lentes propias (sin pisar a los demas): Controller ve la perdida del periodo,
Treasury el runway, FP&A la varianza vs plan; Strategic ve la TRAYECTORIA y la
EFICIENCIA (si crecer alcanza, y a que costo de capital).

Nota de honestidad: el crecimiento se anualiza componiendo el promedio MoM
(proxy; no hay historico YoY con 5 meses). Se documenta como proxy.

Numeros por codigo; el modelo razona y redacta, nunca inventa una cifra.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python strategic_finance_agent.py
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

BURN_MULTIPLE_MAX = 2.0   # Bessemer: >2 es ineficiente
MAGIC_MIN = 0.75          # >0.75 es buen retorno comercial
STRUCTURAL_GAP_PP = 20.0  # brecha de margen tan grande que crecer no la cierra


def agent(system, prompt, max_tokens=600):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


# --- Capa deterministica: las metricas las calcula finance_core ---------
# (numeros sin cliente, testeables por el eval harness; el agente solo narra)

def strategic_escalations(m):
    """Flags estrategicos por severidad. Lista de [sev, mensaje].

    Solo riesgos de eficiencia/trayectoria que ningun otro agente cubre
    (Controller ve la perdida del periodo; Treasury el runway). Aca: eficiencia
    de capital y si el modelo llega a breakeven creciendo.
    """
    out = []
    bm = m["burn_multiple"]
    if bm is not None and bm > BURN_MULTIPLE_MAX:
        out.append(["HIGH", f"burn multiple {bm:.1f}x (> {BURN_MULTIPLE_MAX:.0f}): {bm:.1f} burned "
                            f"per USD of new revenue; low capital efficiency"])
    # Estructural por MAGNITUD, no por el signo del periodo (el operating loss lo
    # escala el Controller): solo cuando la brecha de margen es tan grande que
    # crecer no la cierra. Asi el trigger no se solapa con el del Controller.
    if m["breakeven_gap_pp"] > STRUCTURAL_GAP_PP:
        out.append(["HIGH", f"growth alone won't reach breakeven: a {m['breakeven_gap_pp']:.0f} pp "
                            f"margin gap (operating margin {m['op_margin']*100:.0f}%) won't close on "
                            f"volume; needs structural margin improvement, not more growth"])
    return out


# --- Orquestacion del agente -------------------------------------------

def run(ctx=None, period="2026-05"):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Strategic Finance", "start", "run-rate, capital efficiency, path to breakeven")

    # Strategic metrics run on the P&L series UP TO the close period (the run-rate,
    # growth and burn are anchored on that month). Defaults to the full series.
    periods = [p for p in fc.PERIODS if p <= period] or fc.PERIODS
    m = fc.strategic_metrics(periods)
    esc = strategic_escalations(m)

    bm_txt = f"{m['burn_multiple']:.1f}x" if m["burn_multiple"] is not None else "n/a"
    mg_txt = f"{m['magic_number']:.2f}" if m["magic_number"] is not None else "n/a"
    scen_txt = "\n".join(
        f"  {s['name']}: MoM growth {s['mom_growth']*100:.1f}% -> 12m run-rate {_money(s['run_rate_12m'])}, "
        f"Rule of 40 {s['rule_of_40']:.0f}" for s in m["scenarios"])
    facts = (
        f"Strategic metrics (close {periods[-1]}, USD):\n"
        f"  Run-rate (ARR proxy): {_money(m['run_rate'])}\n"
        f"  Annualized growth (MoM proxy): {m['ann_growth']*100:.0f}%\n"
        f"  Operating margin: {m['op_margin']*100:.0f}%\n"
        f"  Rule of 40: {m['rule_of_40']:.0f} (threshold 40)\n"
        f"  Burn multiple: {bm_txt} (threshold <= {BURN_MULTIPLE_MAX:.0f})\n"
        f"  Magic number: {mg_txt} (good > {MAGIC_MIN})\n"
        f"  Spend mix (% revenue): COGS {m['mix']['cogs']*100:.0f}, S&M {m['mix']['sm']*100:.0f}, "
        f"R&D {m['mix']['rd']*100:.0f}, G&A {m['mix']['ga']*100:.0f}\n"
        f"  Margin gap to breakeven: {m['breakeven_gap_pp']:.0f} pp\n"
        f"Growth scenarios (margin held constant):\n{scen_txt}"
    )
    narrative = agent(
        "You are the Strategic Finance lead. With the metrics given, write 3-5 bullets, CFO "
        "tone: growth quality (Rule of 40, magic number), capital efficiency (burn multiple), "
        "and the path to profitability (margin gap, scenarios). Close with the main strategic "
        "lever. Use only the numbers given; do not invent figures. Write in English.",
        facts,
    )

    ctx.put("Strategic Finance", {
        "metrics": m, "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Strategic Finance", "ok",
              f"run-rate {_money(m['run_rate'])}, Rule of 40 {m['rule_of_40']:.0f}, "
              f"burn multiple {bm_txt}; {len(esc)} escalation(s)")

    if own:
        print("\n--- STRATEGIC FINANCE ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
