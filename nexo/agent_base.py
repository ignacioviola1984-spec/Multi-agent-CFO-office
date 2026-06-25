"""
agent_base.py - Shared scaffolding for the five agents.

Each agent follows the same shape: deterministic detection (cartera_core) ->
deterministic confidence + severity -> the LLM (or template) drafts the Spanish
message -> push to the inbox as `pendiente`. This module holds the pieces they
share so each agent file stays focused on its own rule and prompt.

Nothing here computes a domain number from the model: confidence and severity are
pure functions of the detector payload.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import llm
import review


def first_name(nombre: str) -> str:
    return (nombre or "").split(" ")[0] if nombre else "cliente"


def format_ars(x) -> str:
    """Argentine peso formatting: dot thousands separator. 49850 -> 'ARS 49.850'."""
    return "ARS " + f"{int(round(float(x))):,}".replace(",", ".")


def contact_completeness(cand) -> float:
    """How complete the client's reachability/identity data is (0..1). Feeds the
    confidence score - we are more confident proposing outreach we can deliver."""
    return cc.data_completeness([cand.get("email"), cand.get("telefono"),
                                 cand.get("nombre")])


def cap(ctx, agente, candidates, limit):
    """Keep the top `limit` candidates (already sorted best-first) and LOG how many
    were omitted. Never a silent truncation."""
    if limit and len(candidates) > limit:
        omitted = len(candidates) - limit
        ctx.audit(agente, "CAP",
                  f"{len(candidates)} candidatos; se proponen los {limit} de mayor "
                  f"confianza, {omitted} omitidos (ajustable con limit)")
        return candidates[:limit]
    return candidates


def emit(ctx, *, tipo, agente, cliente_id, nombre, ref, poliza,
         detalle, confianza, severidad, datos,
         system, user_prompt, allowed_numbers, fallback,
         email=None, telefono=None):
    """Draft the message (LLM or template, always grounded), build the Action and
    submit it to the inbox as pendiente. Returns the Action."""
    drafted = llm.draft(system, user_prompt,
                        allowed_numbers=allowed_numbers, fallback=fallback)
    action = review.Action(
        id=review.make_id(tipo, cliente_id, ref),
        tipo=tipo, agente=agente,
        cliente_id=cliente_id, cliente_nombre=nombre,
        detalle=detalle, confianza=round(float(confianza), 4),
        severidad=severidad, datos=datos,
        mensaje_propuesto=drafted["text"], poliza=poliza,
        email=email, telefono=telefono,
    )
    # Record which source wrote the message (llm / template / guarded fallback),
    # so the audit trail never passes a template off as the model's, and a guard
    # rejection is visible. Persist the allowed-number contract so the grounding
    # eval checks the message against exactly what the agent permitted.
    action.datos = {**datos, "_mensaje_source": drafted["source"],
                    "_allowed_numbers": [str(n) for n in allowed_numbers]}
    review.add_action(ctx, action)
    return action


def run_standalone(run_fn, agent_label):
    """Boilerplate so each agent file is runnable on its own for testing:
    load the demo cartera, run the agent into a fresh context, print the inbox."""
    import shared_state
    cart = cc.load_cartera()
    ctx = shared_state.CarteraContext(fresh_audit=True)
    actions = run_fn(cart, ctx)
    print(f"\n{agent_label}: {len(actions)} acciones propuestas (pendientes)\n")
    for a in actions[:8]:
        print(f"  [{a.severidad:5}] conf {a.confianza:.0%} · {a.cliente_nombre} · {a.detalle}")
        print(f"      {a.mensaje_propuesto}\n")
    return ctx
