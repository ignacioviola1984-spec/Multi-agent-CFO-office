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
flags al estado compartido; el board pack y el unico gate humano los hace el
CFO, para no duplicar gates.

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

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIODS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
FORECAST_PERIOD = "2026-06"
LINES = ["revenue", "cogs", "gross", "opex", "operating_income"]


def agent(system, prompt, max_tokens=600):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# --- Capa deterministica (numeros por codigo) ---------------------------

def pnl_series():
    return {p: fc.pnl_usd(p) for p in PERIODS}


def _avg_mom_growth(values):
    growths = [(values[i] / values[i - 1] - 1) for i in range(1, len(values))
               if values[i - 1]]
    return sum(growths) / len(growths) if growths else 0.0


def build_forecast(series):
    """Forecast del proximo periodo. Metodo: revenue, cogs y opex se
    proyectan con su crecimiento mensual promedio; gross y operating income
    se derivan. Metodo explicito y reproducible."""
    rev = [series[p]["revenue"] for p in PERIODS]
    cogs = [series[p]["cogs"] for p in PERIODS]
    opex = [series[p]["opex"] for p in PERIODS]
    g_rev, g_cogs, g_opex = _avg_mom_growth(rev), _avg_mom_growth(cogs), _avg_mom_growth(opex)
    f_rev = rev[-1] * (1 + g_rev)
    f_cogs = cogs[-1] * (1 + g_cogs)
    f_opex = opex[-1] * (1 + g_opex)
    f_gross = f_rev - f_cogs
    f_op = f_gross - f_opex
    return {
        "method": "crecimiento mensual promedio por linea; gross y op income derivados",
        "growth_rev": g_rev, "growth_cogs": g_cogs, "growth_opex": g_opex,
        "revenue": f_rev, "cogs": f_cogs, "gross": f_gross,
        "opex": f_opex, "operating_income": f_op,
    }


def build_variance(series):
    last, prev = series[PERIODS[-1]], series[PERIODS[-2]]
    out = {}
    for k in LINES:
        delta = last[k] - prev[k]
        pct = (delta / abs(prev[k]) * 100) if prev[k] else 0.0
        out[k] = {"prev": prev[k], "last": last[k], "delta": delta, "pct": pct}
    return out


def detect_anomalies(series, variance):
    last, prev = series[PERIODS[-1]], series[PERIODS[-2]]
    anomalies = []
    for k in LINES:
        if abs(variance[k]["pct"]) > 15:
            anomalies.append(f"{k}: movimiento MoM de {variance[k]['pct']:+.1f}%")
    gm_last = last["gross"] / last["revenue"] * 100 if last["revenue"] else 0
    gm_prev = prev["gross"] / prev["revenue"] * 100 if prev["revenue"] else 0
    if abs(gm_last - gm_prev) > 2:
        anomalies.append(f"margen bruto cambio {gm_last - gm_prev:+.1f} pp ({gm_prev:.1f}% -> {gm_last:.1f}%)")
    if last["operating_income"] < 0:
        anomalies.append(f"resultado operativo negativo: {last['operating_income']:,.0f} USD")
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
            out.append(["ALTA", f"revenue {v['var']:+,.0f} USD ({v['var_pct']:+.1f}%) por debajo del plan"])
        elif v["kind"] == "cost":
            out.append(["ALTA", f"sobregasto en {v['label']}: {v['var']:+,.0f} USD ({v['var_pct']:+.1f}%) vs plan"])
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
        return input("  Aprobas el board pack y las acciones? [s/N]: ").strip().lower() == "s"
    except EOFError:
        return False


def run(ctx=None):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("FP&A", "inicio", "forecast, variance MoM, variance vs presupuesto y anomalias")

    series = pnl_series()
    forecast = build_forecast(series)
    variance = build_variance(series)                  # MoM (mes vs mes anterior)
    anomalies = detect_anomalies(series, variance)
    budget = build_budget_variance(PERIODS[-1])        # actual vs plan
    escalations = fpa_escalations(budget["material"])

    ctx.put("FP&A", {
        "forecast": forecast, "variance_mom": variance, "anomalies": anomalies,
        "budget_variance": budget, "escalations": escalations,
    })
    ctx.audit("FP&A", "ok", f"forecast {FORECAST_PERIOD}: rev {_money(forecast['revenue'])}, op {_money(forecast['operating_income'])}")
    ctx.audit("FP&A", "ok", f"{len(anomalies)} anomalia(s) MoM; {len(budget['material'])} linea(s) material(es) vs presupuesto")

    # Numeros como texto para que el modelo explique sin inventar.
    var_txt = "\n".join(
        f"  {k}: {_money(variance[k]['prev'])} -> {_money(variance[k]['last'])} "
        f"({variance[k]['pct']:+.1f}%)" for k in LINES)
    fc_txt = (f"Forecast {FORECAST_PERIOD}: revenue {_money(forecast['revenue'])}, "
              f"gross {_money(forecast['gross'])}, opex {_money(forecast['opex'])}, "
              f"operating income {_money(forecast['operating_income'])}. "
              f"Metodo: {forecast['method']}.")
    anom_txt = "\n".join(f"  - {a}" for a in anomalies) or "  (sin anomalias)"
    bud_txt = (f"Varianza vs presupuesto {PERIODS[-1]} (USD; 'F' favorable, 'U' desfavorable):\n"
               + _budget_table(budget["rows"]))
    bud_txt += ("\n\nLineas materiales (>=5% y >=USD 20k):\n" + _budget_table(budget["material"])
                if budget["material"] else "\n\nNinguna linea supera el umbral de materialidad.")

    variance_expl = agent(
        "Sos analista de FP&A. Explicas variaciones con causas plausibles de negocio, "
        "en 3-4 bullets. Usas solo los numeros que te dan; no inventas cifras.",
        f"Variacion MoM ({PERIODS[-2]} -> {PERIODS[-1]}):\n{var_txt}\n\nExplica los drivers principales.")

    budget_expl = agent(
        "Sos analista de FP&A. Explicas la varianza vs presupuesto en 3-4 bullets: los "
        "drivers favorables y desfavorables principales y su implicancia. Usas solo los "
        "numeros dados; no inventas cifras. 'F' es favorable, 'U' desfavorable.",
        f"{bud_txt}\n\nExplica la varianza vs el plan.")

    anomaly_expl = agent(
        "Sos analista de FP&A enfocado en riesgo. Explicas cada anomalia y su implicancia "
        "en 1-2 frases por item. Solo los numeros dados.",
        f"Anomalias detectadas:\n{anom_txt}\n\nExplica cada una y su implicancia.")

    ctx.put("FP&A", {"variance_expl": variance_expl, "budget_expl": budget_expl,
                     "anomaly_expl": anomaly_expl})

    # Board pack + acciones + HITL: solo en modo standalone. Bajo el CFO
    # orquestador, FP&A entrega su analisis y el CFO hace el board pack y el
    # unico gate humano (no se duplican los gates).
    if own:
        board_pack = agent(
            "Sos quien redacta el board pack. Resumen ejecutivo de 5-7 frases, tono CFO, "
            "directo, sin relleno. No agregues numeros nuevos.",
            f"{fc_txt}\n\nVariacion MoM:\n{variance_expl}\n\nVarianza vs presupuesto:\n{budget_expl}\n\n"
            f"Anomalias:\n{anomaly_expl}\n\nRedacta el board pack del periodo.")
        actions = agent(
            "Sos el FP&A lead. Propones 3 acciones concretas y accionables a partir de los "
            "hallazgos, priorizadas. Una linea cada una. No agregues numeros nuevos; usa "
            "solo las cifras dadas.",
            f"Forecast y hallazgos:\n{fc_txt}\n\n{budget_expl}\n\n{anomaly_expl}\n\n"
            "Propone 3 acciones priorizadas.")

        print("\n--- BOARD PACK (borrador) ---\n" + board_pack)
        print("\n--- ACCIONES PROPUESTAS (borrador) ---\n" + actions)

        if hitl_gate("Revisa el board pack y las acciones antes de fijarlas."):
            ctx.put("FP&A", {"board_pack": board_pack, "actions": actions, "status": "approved"})
            ctx.audit("FP&A", "aprobado", "board pack y acciones fijadas por el humano")
        else:
            ctx.put("FP&A", {"status": "rejected"})
            ctx.audit("FP&A", "RECHAZADO", "el humano no aprobo; board pack no fijado")

        path = ctx.save()
        print(f"\nEstado compartido guardado en: {os.path.basename(path)}")
    else:
        ctx.audit("FP&A", "ok", "analisis entregado al CFO (board pack y gate los hace el orquestador)")
    return ctx


if __name__ == "__main__":
    run()
