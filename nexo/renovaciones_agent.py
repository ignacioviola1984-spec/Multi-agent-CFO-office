"""
renovaciones_agent.py - Agent 1: upcoming renewals.

Detects active policies whose renewal falls within N days (default 30), and
proposes a reminder + pre-drafted renewal message per policy. Urgency drives the
deterministic severity and confidence; the LLM only writes the message body.

Standalone:  python nexo/renovaciones_agent.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import agent_base as ab

AGENTE = "renovaciones_agent"
TIPO = "renovacion"

SYSTEM = (
    "Sos un agente de renovaciones de una correduría de seguros. Redactás un "
    "recordatorio breve y cordial para que el cliente renueve su póliza a tiempo y "
    "no quede sin cobertura. Invitás a coordinar la renovación. Mencionás en cuántos "
    "días vence sólo si se te da ese dato."
)


def _strength(dias: int) -> float:
    if dias <= 7:
        return 0.90
    if dias <= 15:
        return 0.80
    return 0.70


def _severidad(dias: int) -> str:
    if dias <= 7:
        return "ALTA"
    if dias <= 15:
        return "MEDIA"
    return "BAJA"


def run(cart, ctx, days: int = 30, limit=None):
    raw = cart.policies_expiring(days)
    # enrich with deterministic confidence + severity, then sort best-first
    cands = []
    for c in raw:
        dias = c["dias_para_vencer"]
        conf = cc.confidence(ab.contact_completeness(c), _strength(dias))
        cands.append({**c, "_conf": conf, "_sev": _severidad(dias), "_strength": _strength(dias)})
    cands.sort(key=lambda r: (-r["_conf"], r["dias_para_vencer"], r["cliente_id"]))
    cands = ab.cap(ctx, AGENTE, cands, limit)

    actions = []
    for c in cands:
        dias = c["dias_para_vencer"]
        nombre = c["nombre"]
        fn = ab.first_name(nombre)
        detalle = (f"{c['ramo']} con {c['aseguradora']} vence en {dias} días "
                   f"(prima {ab.format_ars(c['prima_mensual'])}/mes)")
        # The body may cite only the day count; everything else is qualitative.
        fallback = (
            f"Hola {fn}, ¿cómo estás? Te escribo para recordarte que tu póliza de "
            f"{c['ramo']} con {c['aseguradora']} vence en {dias} días. Me gustaría "
            f"coordinar la renovación para que sigas cubierto sin interrupciones. "
            f"¿Te queda cómodo si lo vemos esta semana?"
        )
        prompt = (
            f"Cliente: {fn}. Ramo: {c['ramo']}. Aseguradora: {c['aseguradora']}. "
            f"Vence en {dias} días. Escribí el recordatorio de renovación."
        )
        actions.append(ab.emit(
            ctx, tipo=TIPO, agente=AGENTE, cliente_id=c["cliente_id"], nombre=nombre,
            ref=c["numero_poliza"], poliza=c["numero_poliza"], detalle=detalle,
            confianza=c["_conf"], severidad=c["_sev"],
            datos={"dias_para_vencer": dias, "ramo": c["ramo"],
                   "aseguradora": c["aseguradora"], "prima_mensual": c["prima_mensual"],
                   "fecha_vencimiento": c["fecha_vencimiento"]},
            system=SYSTEM, user_prompt=prompt, allowed_numbers=[dias], fallback=fallback,
            email=c["email"], telefono=c["telefono"],
        ))

    ctx.put(AGENTE, {"detectados": len(raw), "propuestos": len(actions), "ventana_dias": days})
    n_alta = sum(1 for a in actions if a.severidad == "ALTA")
    if n_alta:
        ctx.flag(AGENTE, "MEDIA", f"{n_alta} renovaciones urgentes (vencen en <= 7 días)")
    return actions


if __name__ == "__main__":
    ab.run_standalone(run, "Renovaciones")
