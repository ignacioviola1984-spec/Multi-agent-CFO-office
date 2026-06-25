"""
cross_sell_agent.py - Agent 4: cross-sell.

Detects clients holding one ramo but missing a complementary one (Auto without
Hogar, Comercio without ART, Hogar without Vida) and proposes a cross-sell
message. The rule's strength drives the deterministic confidence; the message is
qualitative (no figures).

There are usually many cross-sell openings in a portfolio, so the orchestrator
passes a `limit` (logged) to keep the inbox usable. Standalone runs propose all.

Standalone:  python nexo/cross_sell_agent.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import agent_base as ab

AGENTE = "cross_sell_agent"
TIPO = "cross_sell"

SYSTEM = (
    "Sos un productor de seguros que detecta una oportunidad de complementar la "
    "cobertura de un cliente. Redactás una propuesta breve, sin presión, que parte "
    "de un ramo que el cliente YA tiene con vos y sugiere sumar el ramo "
    "complementario que se te indica. No menciones cifras ni precios."
)


def run(cart, ctx, limit=None):
    raw = cart.cross_sell_candidates()
    cands = []
    for c in raw:
        conf = cc.confidence(ab.contact_completeness(c), c["strength"])
        sev = "MEDIA" if c["strength"] >= 0.85 else "BAJA"
        cands.append({**c, "_conf": conf, "_sev": sev})
    cands.sort(key=lambda r: (-r["_conf"], r["cliente_id"], r["missing_ramo"]))
    cands = ab.cap(ctx, AGENTE, cands, limit)

    actions = []
    for c in cands:
        fn = ab.first_name(c["nombre"])
        detalle = (f"Tiene {c['has_ramo']} (activa, {c['base_aseguradora']}) y no tiene "
                   f"{c['missing_ramo']} · oportunidad de {c['missing_ramo']}")
        fallback = (
            f"Hola {fn}, ¿cómo estás? Ya confiás en nosotros para tu seguro de "
            f"{c['has_ramo']}, así que quería comentarte una idea: muchos clientes en "
            f"tu situación suman {c['missing_ramo']} para quedar mejor cubiertos "
            f"({c['rationale']}). Si te interesa, te preparo una propuesta sin compromiso."
        )
        prompt = (
            f"Cliente: {fn}. Ya tiene: {c['has_ramo']}. Le proponemos sumar: "
            f"{c['missing_ramo']}. Motivo: {c['rationale']}. "
            f"Escribí la propuesta de cross-sell (sin cifras)."
        )
        actions.append(ab.emit(
            ctx, tipo=TIPO, agente=AGENTE, cliente_id=c["cliente_id"], nombre=c["nombre"],
            ref=c["missing_ramo"], poliza=c["base_poliza"], detalle=detalle,
            confianza=c["_conf"], severidad=c["_sev"],
            datos={"has_ramo": c["has_ramo"], "missing_ramo": c["missing_ramo"],
                   "strength": c["strength"], "base_poliza": c["base_poliza"]},
            system=SYSTEM, user_prompt=prompt, allowed_numbers=[], fallback=fallback,
            email=c["email"], telefono=c["telefono"],
        ))

    ctx.put(AGENTE, {"detectados": len(raw), "propuestos": len(actions)})
    return actions


if __name__ == "__main__":
    ab.run_standalone(run, "Cross-sell")
