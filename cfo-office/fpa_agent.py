"""
fpa_agent.py - FP&A Agent del CFO office (etapa 1).

Hace, en orden:
  1) Forecast del proximo periodo (metodo explicito, por codigo).
  2) Variance MoM: ultimo periodo vs el anterior (por codigo).
  3) Variance vs presupuesto: actual vs plan, con drivers materiales (por codigo).
  4) Deteccion de anomalias por reglas (por codigo).
  5) Explicacion de variances y anomalias (Claude, sobre numeros dados).
  6) Board pack + acciones propuestas + HITL: SOLO en modo standalone.

Numeros por codigo (deterministicos, reusa finance_core). El modelo razona
y redacta; nunca inventa una cifra. Deja todo en el estado compartido.

Bajo el CFO orquestador (run con un ctx dado), FP&A entrega su analisis y sus
flags; en el modelo por etapas lo firma el FP&A Director (primera linea), y el
board pack + la firma FINAL del CFO los maneja el orquestador. Por eso FP&A NO
corre su propio gate ahi (solo lo hace en modo standalone, para prueba aislada).

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python fpa_agent.py
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

PERIODS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
FORECAST_PERIOD = "2026-06"
LINES = ["revenue", "cogs", "gross", "opex", "operating_income"]


def _next_period(period):
    """The YYYY-MM month right after `period` (the one FP&A forecasts)."""
    y, m = (int(x) for x in period.split("-"))
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def periods_through(period):
    """The P&L series up to and including the close period (defaults to all)."""
    return [p for p in PERIODS if p <= period] or PERIODS


def agent(system, prompt, max_tokens=600):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# --- Capa deterministica (numeros por codigo) ---------------------------

def pnl_series(periods=None):
    return {p: fc.pnl_usd(p) for p in (periods or PERIODS)}


def _avg_mom_growth(values):
    growths = [(values[i] / values[i - 1] - 1) for i in range(1, len(values))
               if values[i - 1]]
    return sum(growths) / len(growths) if growths else 0.0


def build_forecast(series, periods=None):
    """Forecast del proximo periodo. Metodo: revenue, cogs y opex se
    proyectan con su crecimiento mensual promedio; gross y operating income
    se derivan. Metodo explicito y reproducible."""
    periods = periods or PERIODS
    rev = [series[p]["revenue"] for p in periods]
    cogs = [series[p]["cogs"] for p in periods]
    opex = [series[p]["opex"] for p in periods]
    g_rev, g_cogs, g_opex = _avg_mom_growth(rev), _avg_mom_growth(cogs), _avg_mom_growth(opex)
    f_rev = rev[-1] * (1 + g_rev)
    f_cogs = cogs[-1] * (1 + g_cogs)
    f_opex = opex[-1] * (1 + g_opex)
    f_gross = f_rev - f_cogs
    f_op = f_gross - f_opex
    return {
        "method": "average month-over-month growth per line; gross and op income derived",
        "growth_rev": g_rev, "growth_cogs": g_cogs, "growth_opex": g_opex,
        "revenue": f_rev, "cogs": f_cogs, "gross": f_gross,
        "opex": f_opex, "operating_income": f_op,
    }


def build_variance(series, periods=None):
    periods = periods or PERIODS
    last, prev = series[periods[-1]], series[periods[-2]]
    out = {}
    for k in LINES:
        delta = last[k] - prev[k]
        pct = (delta / abs(prev[k]) * 100) if prev[k] else 0.0
        out[k] = {"prev": prev[k], "last": last[k], "delta": delta, "pct": pct}
    return out


def detect_anomalies(series, variance, periods=None):
    periods = periods or PERIODS
    last, prev = series[periods[-1]], series[periods[-2]]
    anomalies = []
    for k in LINES:
        if abs(variance[k]["pct"]) > 15:
            anomalies.append(f"{k}: MoM move of {variance[k]['pct']:+.1f}%")
    gm_last = last["gross"] / last["revenue"] * 100 if last["revenue"] else 0
    gm_prev = prev["gross"] / prev["revenue"] * 100 if prev["revenue"] else 0
    if abs(gm_last - gm_prev) > 2:
        anomalies.append(f"gross margin moved {gm_last - gm_prev:+.1f} pp ({gm_prev:.1f}% -> {gm_last:.1f}%)")
    if last["operating_income"] < 0:
        anomalies.append(f"negative operating income: {last['operating_income']:,.0f} USD")
    return anomalies


def build_budget_variance(period):
    """Varianza vs presupuesto (actual vs plan). Numeros por codigo: reusa
    finance_core, que ya valida F/U por tipo de linea y la materialidad."""
    return {"rows": fc.variance_usd(period), "material": fc.material_variances(period)}


# Subtotales (rollups) de la varianza: son sumas de las lineas de detalle, asi
# que escalarlos ademas de sus componentes duplicaria los mismos dolares.
_VAR_SUBTOTALS = {"Gross profit", "Total opex", "Operating income"}


def fpa_escalations(material):
    """Escala las varianzas presupuestarias DESFAVORABLES y materiales.

    Solo lo desfavorable es un riesgo (un favorable no se escala). Escala SOLO
    las lineas de detalle (revenue y cada linea de costo), nunca los subtotales:
    'Total opex' es la suma de S&M/R&D/G&A y 'Operating income' es el neto de
    todo, asi que escalarlos duplicaria los mismos dolares. Ademas, el resultado
    operativo lo escala el Controller (perdida operativa); FP&A se queda con los
    drivers vs plan. Asi cada riesgo tiene un unico dueno.
    """
    out = []
    for v in material:
        if v["flag"] != "U" or v["label"] in _VAR_SUBTOTALS:
            continue
        if v["label"] == "Revenue":
            out.append(["HIGH", f"revenue {v['var']:+,.0f} USD ({v['var_pct']:+.1f}%) below plan"])
        elif v["kind"] == "cost":
            out.append(["HIGH", f"overspend on {v['label']}: {v['var']:+,.0f} USD ({v['var_pct']:+.1f}%) vs plan"])
    return out


def _money(x):
    return f"USD {x:,.0f}"


def _budget_table(rows):
    return "\n".join(
        f"  {v['label']}: budget {v['budget']:,.0f}, actual {v['actual']:,.0f}, "
        f"var {v['var']:+,.0f} ({v['var_pct']:+.1f}%) [{v['flag']}]" for v in rows)


# --- Orquestacion del agente -------------------------------------------

def hitl_gate(prompt_txt):
    print("\n  [human-in-the-loop] " + prompt_txt)
    try:
        return input("  Approve the board pack and actions? [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


def run(ctx=None, period="2026-05"):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("FP&A", "start", "forecast, MoM variance, budget variance and anomalies")

    periods = periods_through(period)                  # P&L series up to the close
    forecast_period = _next_period(periods[-1])
    series = pnl_series(periods)
    forecast = build_forecast(series, periods)
    variance = build_variance(series, periods)         # MoM (mes vs mes anterior)
    anomalies = detect_anomalies(series, variance, periods)
    budget = build_budget_variance(periods[-1])        # actual vs plan
    escalations = fpa_escalations(budget["material"])

    ctx.put("FP&A", {
        "forecast": forecast, "variance_mom": variance, "anomalies": anomalies,
        "budget_variance": budget, "escalations": escalations,
    })
    ctx.audit("FP&A", "ok", f"forecast {forecast_period}: rev {_money(forecast['revenue'])}, op {_money(forecast['operating_income'])}")
    ctx.audit("FP&A", "ok", f"{len(anomalies)} MoM anomaly(ies); {len(budget['material'])} material line(s) vs budget")

    # Numbers as text so the model can explain without inventing.
    var_txt = "\n".join(
        f"  {k}: {_money(variance[k]['prev'])} -> {_money(variance[k]['last'])} "
        f"({variance[k]['pct']:+.1f}%)" for k in LINES)
    fc_txt = (f"Forecast {forecast_period}: revenue {_money(forecast['revenue'])}, "
              f"gross {_money(forecast['gross'])}, opex {_money(forecast['opex'])}, "
              f"operating income {_money(forecast['operating_income'])}. "
              f"Method: {forecast['method']}.")
    anom_txt = "\n".join(f"  - {a}" for a in anomalies) or "  (no anomalies)"
    bud_txt = (f"Budget variance {periods[-1]} (USD; 'F' favorable, 'U' unfavorable):\n"
               + _budget_table(budget["rows"]))
    bud_txt += ("\n\nMaterial lines (>=5% and >=USD 20k):\n" + _budget_table(budget["material"])
                if budget["material"] else "\n\nNo line exceeds the materiality threshold.")

    variance_expl = agent(
        "You are an FP&A analyst. Explain the variances with plausible business causes, in "
        "3-4 bullets. Use only the numbers given; do not invent figures. Write in English.",
        f"MoM variance ({periods[-2]} -> {periods[-1]}):\n{var_txt}\n\nExplain the main drivers.")

    budget_expl = agent(
        "You are an FP&A analyst. Explain the budget variance in 3-4 bullets: the main "
        "favorable and unfavorable drivers and their implication. Use only the numbers given; "
        "do not invent figures. 'F' is favorable, 'U' unfavorable. Write in English.",
        f"{bud_txt}\n\nExplain the variance vs the plan.")

    anomaly_expl = agent(
        "You are a risk-focused FP&A analyst. Explain each anomaly and its implication in 1-2 "
        "sentences per item. Only the numbers given. Write in English.",
        f"Detected anomalies:\n{anom_txt}\n\nExplain each one and its implication.")

    ctx.put("FP&A", {"variance_expl": variance_expl, "budget_expl": budget_expl,
                     "anomaly_expl": anomaly_expl})

    # Board pack + acciones + HITL: solo en modo standalone. Bajo el CFO
    # orquestador, FP&A entrega su analisis (lo firma el FP&A Director en la
    # primera linea) y el CFO hace el board pack y la firma FINAL.
    if own:
        board_pack = agent(
            "You write the board pack. Executive summary of 5-7 sentences, CFO tone, direct, "
            "no filler. Do not add new numbers. Write in English.",
            f"{fc_txt}\n\nMoM variance:\n{variance_expl}\n\nBudget variance:\n{budget_expl}\n\n"
            f"Anomalies:\n{anomaly_expl}\n\nWrite the board pack for the period.")
        actions = agent(
            "You are the FP&A lead. Propose 3 concrete, actionable, prioritized actions from the "
            "findings. One line each. Do not add new numbers; use only the figures given. "
            "Write in English.",
            f"Forecast and findings:\n{fc_txt}\n\n{budget_expl}\n\n{anomaly_expl}\n\n"
            "Propose 3 prioritized actions.")

        print("\n--- BOARD PACK (draft) ---\n" + board_pack)
        print("\n--- PROPOSED ACTIONS (draft) ---\n" + actions)

        if hitl_gate("Review the board pack and actions before fixing them."):
            ctx.put("FP&A", {"board_pack": board_pack, "actions": actions, "status": "approved"})
            ctx.audit("FP&A", "approved", "board pack and actions fixed by the human")
        else:
            ctx.put("FP&A", {"status": "rejected"})
            ctx.audit("FP&A", "REJECTED", "human did not approve; board pack not fixed")

        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    else:
        ctx.audit("FP&A", "ok", "analysis delivered to the CFO (board pack and gate handled by the orchestrator)")
    return ctx


if __name__ == "__main__":
    run()
