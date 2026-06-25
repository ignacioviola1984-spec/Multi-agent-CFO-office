"""
analisis_cartera_agent.py - Agent 5: portfolio analytics + dashboard insight.

Computes deterministic portfolio metrics (total policies, monthly premium,
mix by insurer and by ramo, % en mora, upcoming renewals, active/inactive
clients, retention) and asks the LLM to write the dashboard INSIGHT narrative
from those exact figures. No per-client action - this feeds the dashboard.

The narrative is number-heavy, so the grounding guard is given the formatted
facts + the template as the allowed set: the model may restate the figures it was
shown, nothing else.

Standalone:  python nexo/analisis_cartera_agent.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent_base as ab
import llm

AGENTE = "analisis_cartera_agent"

SYSTEM = (
    "Sos un analista de cartera de una correduría de seguros. Con las métricas que "
    "se te dan, escribís un insight ejecutivo de 3 a 5 frases para el panel del "
    "productor: estado general, riesgos (mora, vencimientos) y oportunidades "
    "(retención, mix). Usás SOLO los números que se te dan; no agregues otros."
)


def _top(d, key):
    """Top entry of a mix dict by the given sub-key; returns (name, sub-dict)."""
    if not d:
        return None, {}
    name = max(d, key=lambda k: d[k][key])
    return name, d[name]


def build_facts(m):
    """The exact figures shown to the model and used in the template (one source)."""
    top_aseg, aseg = _top(m["mix_por_aseguradora"], "prima_mensual")
    top_ramo, ramo = _top(m["mix_por_ramo"], "count")
    return (
        f"Cartera: {m['total_polizas']} pólizas de {m['total_clientes']} clientes "
        f"({m['polizas_activas']} activas).\n"
        f"Prima mensual activa: {ab.format_ars(m['prima_mensual_total'])}. "
        f"Comisión mensual estimada: {ab.format_ars(m['comision_mensual_total'])}.\n"
        f"Mora: {m['pct_en_mora_polizas']:.1f}% de las pólizas activas "
        f"({m['pct_en_mora_prima']:.1f}% de la prima).\n"
        f"Vencimientos próximos ({m['vencimientos_dias']} días): {m['vencimientos_proximos']}.\n"
        f"Clientes activos: {m['clientes_activos']}; inactivos: {m['clientes_inactivos']}. "
        f"Retención: {m['retencion_pct']:.1f}%.\n"
        f"Aseguradora líder por prima: {top_aseg} ({ab.format_ars(aseg['prima_mensual'])}/mes).\n"
        f"Ramo más numeroso: {top_ramo} ({ramo['count']} pólizas)."
    )


def build_template(m):
    """Deterministic insight used offline or when the guard rejects the model."""
    top_aseg, _ = _top(m["mix_por_aseguradora"], "prima_mensual")
    return (
        f"La cartera reúne {m['total_polizas']} pólizas de {m['total_clientes']} "
        f"clientes, con una prima mensual activa de {ab.format_ars(m['prima_mensual_total'])} "
        f"y una comisión estimada de {ab.format_ars(m['comision_mensual_total'])} por mes. "
        f"La mora alcanza al {m['pct_en_mora_polizas']:.1f}% de las pólizas activas: "
        f"es el punto a vigilar de cerca. Hay {m['vencimientos_proximos']} renovaciones "
        f"en los próximos {m['vencimientos_dias']} días, una buena oportunidad para "
        f"fidelizar. La retención de clientes es del {m['retencion_pct']:.1f}%, con "
        f"{top_aseg} como aseguradora líder por prima."
    )


def run(cart, ctx):
    m = cart.portfolio_metrics()
    facts = build_facts(m)
    template = build_template(m)
    drafted = llm.draft(SYSTEM, facts + "\n\nEscribí el insight del panel.",
                        allowed_numbers=[facts, template], fallback=template, max_tokens=500)

    ctx.put(AGENTE, {"metrics": m, "narrative": drafted["text"],
                     "narrative_source": drafted["source"]})
    ctx.state["metrics"] = m
    ctx.audit(AGENTE, "OK", f"métricas de cartera + insight ({drafted['source']})")
    # Surface portfolio-level risk as a flag (no per-client action here).
    if m["pct_en_mora_polizas"] >= 15.0:
        ctx.flag(AGENTE, "MEDIA",
                 f"mora en {m['pct_en_mora_polizas']:.1f}% de las pólizas activas")
    return {"metrics": m, "narrative": drafted["text"]}


if __name__ == "__main__":
    import cartera_core as cc
    import shared_state
    cart = cc.load_cartera()
    ctx = shared_state.CarteraContext(fresh_audit=True)
    out = run(cart, ctx)
    print("\n--- MÉTRICAS DE CARTERA ---")
    print(build_facts(out["metrics"]))
    print("\n--- INSIGHT DEL PANEL ---")
    print(out["narrative"])
