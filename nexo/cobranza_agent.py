"""
cobranza_agent.py - Agent 2: collections (mora).

Detects active policies in arrears, bucketed by days past due (0-30, 31-60,
61-90, 90+), and proposes a collection message whose TONE is tailored per bucket
(gentle reminder -> final notice). The bucket drives the deterministic severity
and confidence; the LLM writes the body.

Standalone:  python nexo/cobranza_agent.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import agent_base as ab

AGENTE = "cobranza_agent"
TIPO = "cobranza"

SYSTEM = (
    "Sos un agente de cobranzas de una correduría de seguros. Redactás un mensaje "
    "para que el cliente regularice una cuota impaga de su póliza, cuidando la "
    "relación. Ajustás el tono a la gravedad del atraso que se te indica. Podés "
    "mencionar los días de atraso sólo si se te dan."
)

# Per-bucket deterministic policy: rule strength, severity and the tone to ask for.
BUCKET = {
    "0-30":  {"strength": 0.60, "sev": "BAJA",  "tono": "recordatorio amable, atraso reciente"},
    "31-60": {"strength": 0.75, "sev": "MEDIA", "tono": "seguimiento firme pero cordial"},
    "61-90": {"strength": 0.85, "sev": "ALTA",  "tono": "urgencia: la cobertura está en riesgo"},
    "90+":   {"strength": 0.95, "sev": "ALTA",  "tono": "aviso final: la póliza puede darse de baja"},
}

_FALLBACK = {
    "0-30": ("Hola {fn}, ¿cómo va? Te recuerdo que quedó pendiente la cuota de tu "
             "póliza de {ramo}. Son {dias} días de atraso, nada grave. ¿Coordinamos "
             "para regularizarla y mantener la cobertura al día?"),
    "31-60": ("Hola {fn}, te escribo por la cuota impaga de tu póliza de {ramo}, que "
              "ya lleva {dias} días de atraso. Te pido que la regularicemos pronto "
              "para que sigas tranquilo con tu cobertura. ¿Lo vemos hoy?"),
    "61-90": ("Hola {fn}, necesito tu ayuda con la cuota de tu póliza de {ramo}: lleva "
              "{dias} días de atraso y la cobertura empieza a estar en riesgo. "
              "Regularicémosla cuanto antes; decime cómo te queda más fácil."),
    "90+": ("Hola {fn}, es importante: la póliza de {ramo} tiene {dias} días de atraso "
            "y puede darse de baja por falta de pago. Quiero evitar que pierdas la "
            "cobertura. Por favor contactame hoy y lo resolvemos juntos."),
}


def run(cart, ctx, limit=None):
    mb = cart.mora_buckets()
    cands = []
    for it in mb["items"]:
        pol = BUCKET[it["bucket"]]
        conf = cc.confidence(ab.contact_completeness(it), pol["strength"])
        cands.append({**it, "_conf": conf, "_sev": pol["sev"], "_tono": pol["tono"]})
    cands.sort(key=lambda r: (-r["dias_mora"], -r["_conf"], r["cliente_id"]))
    cands = ab.cap(ctx, AGENTE, cands, limit)

    actions = []
    for c in cands:
        dias = c["dias_mora"]
        fn = ab.first_name(c["nombre"])
        detalle = (f"En mora {dias} días (tramo {c['bucket']}) · {c['ramo']} con "
                   f"{c['aseguradora']} · prima {ab.format_ars(c['prima_mensual'])}/mes")
        fallback = _FALLBACK[c["bucket"]].format(fn=fn, ramo=c["ramo"], dias=dias)
        prompt = (
            f"Cliente: {fn}. Ramo: {c['ramo']}. Días de atraso: {dias}. "
            f"Tramo de mora: {c['bucket']}. Tono: {c['_tono']}. "
            f"Escribí el mensaje de cobranza."
        )
        actions.append(ab.emit(
            ctx, tipo=TIPO, agente=AGENTE, cliente_id=c["cliente_id"], nombre=c["nombre"],
            ref=c["numero_poliza"], poliza=c["numero_poliza"], detalle=detalle,
            confianza=c["_conf"], severidad=c["_sev"],
            datos={"dias_mora": dias, "bucket": c["bucket"], "ramo": c["ramo"],
                   "aseguradora": c["aseguradora"], "prima_mensual": c["prima_mensual"]},
            system=SYSTEM, user_prompt=prompt, allowed_numbers=[dias], fallback=fallback,
            email=c["email"], telefono=c["telefono"],
        ))

    ctx.put(AGENTE, {"detectados": mb["total_count"], "propuestos": len(actions),
                     "por_bucket": {b: v["count"] for b, v in mb["buckets"].items()},
                     "prima_en_mora": mb["total_prima_mensual"]})
    n90 = mb["buckets"]["90+"]["count"]
    if n90:
        ctx.flag(AGENTE, "ALTA", f"{n90} pólizas con mora 90+ (riesgo de baja por falta de pago)")
    return actions


if __name__ == "__main__":
    ab.run_standalone(run, "Cobranza")
