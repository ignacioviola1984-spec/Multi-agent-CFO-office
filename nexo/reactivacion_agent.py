"""
reactivacion_agent.py - Agent 3: win-back of inactive clients.

Detects clients with no active policy whose coverage lapsed more than M months
ago (default 6) and proposes a personalized reactivation message. The message is
qualitative (no figures) so it stays warm and grounded; recency of the lapse
drives the deterministic confidence.

Standalone:  python nexo/reactivacion_agent.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import agent_base as ab

AGENTE = "reactivacion_agent"
TIPO = "reactivacion"

SYSTEM = (
    "Sos un productor de seguros reconectando con un cliente que dejó de tener "
    "pólizas activas hace un tiempo. Redactás un mensaje cálido y personal para "
    "retomar el vínculo y ofrecer acompañarlo de nuevo. Mencionás el ramo que tenía "
    "si se te da. No menciones cifras ni cantidades de meses exactas."
)


def _strength(dias_inactivo: int) -> float:
    # Recently lapsed clients are easier to win back.
    if dias_inactivo <= 365:
        return 0.70
    if dias_inactivo <= 540:
        return 0.55
    return 0.45


def run(cart, ctx, months: int = 6, limit=None):
    raw = cart.inactive_clients(months)
    cands = []
    for c in raw:
        conf = cc.confidence(ab.contact_completeness(c), _strength(c["dias_inactivo"]))
        sev = "MEDIA" if c["dias_inactivo"] <= 365 else "BAJA"
        cands.append({**c, "_conf": conf, "_sev": sev})
    cands.sort(key=lambda r: (-r["_conf"], r["dias_inactivo"], r["cliente_id"]))
    cands = ab.cap(ctx, AGENTE, cands, limit)

    actions = []
    for c in cands:
        fn = ab.first_name(c["nombre"])
        ramo_prev = c["ramos"][0] if c["ramos"] else "seguros"
        meses = c["dias_inactivo"] // 30  # for the deterministic detalle only
        detalle = (f"Cliente inactivo hace ~{meses} meses · tenía {', '.join(c['ramos'])} "
                   f"con {c['ultima_aseguradora']}")
        fallback = (
            f"Hola {fn}, ¿cómo estás? Hace un tiempo que no nos cruzamos por tus "
            f"seguros. Me encantaría volver a acompañarte y revisar juntos tu seguro "
            f"de {ramo_prev} para dejarte bien cubierto según tu situación de hoy. "
            f"¿Tenés un rato para ponernos al día?"
        )
        prompt = (
            f"Cliente: {fn}. Ramo que tenía: {ramo_prev}. "
            f"Escribí el mensaje de reactivación (sin cifras)."
        )
        actions.append(ab.emit(
            ctx, tipo=TIPO, agente=AGENTE, cliente_id=c["cliente_id"], nombre=c["nombre"],
            ref="*", poliza=None, detalle=detalle,
            confianza=c["_conf"], severidad=c["_sev"],
            datos={"dias_inactivo": c["dias_inactivo"], "ramos": c["ramos"],
                   "ultima_aseguradora": c["ultima_aseguradora"]},
            system=SYSTEM, user_prompt=prompt, allowed_numbers=[], fallback=fallback,
            email=c["email"], telefono=c["telefono"],
        ))

    ctx.put(AGENTE, {"detectados": len(raw), "propuestos": len(actions), "meses_umbral": months})
    return actions


if __name__ == "__main__":
    ab.run_standalone(run, "Reactivacion")
