"""
review.py - The single-broker approval inbox (human-in-the-loop).

Simplified from cfo-office/review.py (maker-checker per function) to a SINGLE
checker: the broker (productor). The agents are the makers — each proposes an
action that starts as `pendiente`. The broker is the only checker; every action
is exported ONLY after they approve (or edit-then-approve). Rejections are logged.
Nothing is ever auto-sent.

Every decision records who / what / when (decided_by, decision_note, ts_decidida)
into the shared state's audit trail — the evidence trail the model is built on.

Status machine:  pendiente -> aprobada | editada | rechazada
(`editada` means the broker changed the message text and approved it.)
"""

import os
import sys
from dataclasses import dataclass, asdict, field, fields
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from shared_state import now_iso

# Status values.
PENDIENTE, APROBADA, EDITADA, RECHAZADA = "pendiente", "aprobada", "editada", "rechazada"
EXPORTABLE = (APROBADA, EDITADA)        # only these are written to the Excel

# Severity ordering for the prioritized inbox (lower = more urgent).
SEVERITY_ORDER = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
DECIDED_BY = "productor"                 # the single broker


@dataclass
class Action:
    """One proposed action awaiting the broker's decision.

    `datos` carries the deterministic numbers behind the action (the figures the
    message is allowed to reference); the grounding guard checks the message
    against it. `mensaje_propuesto` is drafted prose; `mensaje_final` is what gets
    exported (the proposal unless the broker edited it)."""
    id: str
    tipo: str                  # renovacion | cobranza | reactivacion | cross_sell
    agente: str
    cliente_id: str
    cliente_nombre: str
    detalle: str               # deterministic, human-readable summary
    confianza: float           # 0..1, deterministic
    severidad: str             # ALTA | MEDIA | BAJA, deterministic
    datos: dict                # deterministic payload (numbers behind the action)
    mensaje_propuesto: str = ""
    poliza: Optional[str] = None
    estado: str = PENDIENTE
    mensaje_final: Optional[str] = None
    decision_note: str = ""
    decided_by: Optional[str] = None
    ts_creada: str = field(default_factory=now_iso)
    ts_decidida: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def make_id(tipo, cliente_id, ref):
    """Stable, unique action id (so re-runs/replay produce identical ids)."""
    return f"{tipo}|{cliente_id}|{ref}"


# --------------------------------------------------------------------------
# Inbox operations over the context's state["inbox"].
# --------------------------------------------------------------------------

def add_action(ctx, action: Action):
    """A maker (agent) submits a proposed action; it enters as pendiente."""
    ctx.state["inbox"].append(action.to_dict())
    ctx.audit(action.agente, "PROPUESTA",
              f"{action.tipo} · {action.cliente_nombre} · conf {action.confianza:.0%} · {action.detalle}")
    return action.id


def _find(ctx, action_id):
    for d in ctx.state["inbox"]:
        if d["id"] == action_id:
            return d
    raise KeyError(f"accion no encontrada: {action_id}")


def list_actions(ctx, estado=None, tipo=None):
    out = ctx.state["inbox"]
    if estado is not None:
        out = [d for d in out if d["estado"] == estado]
    if tipo is not None:
        out = [d for d in out if d["tipo"] == tipo]
    return out


def pending(ctx):
    """All actions still awaiting a decision."""
    return list_actions(ctx, estado=PENDIENTE)


def approve(ctx, action_id, note=""):
    """Broker approves the proposal as-is. mensaje_final = the proposed message."""
    d = _find(ctx, action_id)
    d["estado"] = APROBADA
    d["mensaje_final"] = d.get("mensaje_final") or d["mensaje_propuesto"]
    d["decision_note"] = note
    d["decided_by"] = DECIDED_BY
    d["ts_decidida"] = now_iso()
    ctx.audit(DECIDED_BY, "APROBADA", f"{d['tipo']} · {d['cliente_nombre']}" + (f" · {note}" if note else ""))
    return d


def edit(ctx, action_id, nuevo_mensaje, note=""):
    """Broker edits the message text and approves it (status = editada)."""
    d = _find(ctx, action_id)
    d["estado"] = EDITADA
    d["mensaje_final"] = nuevo_mensaje
    d["decision_note"] = note
    d["decided_by"] = DECIDED_BY
    d["ts_decidida"] = now_iso()
    ctx.audit(DECIDED_BY, "EDITADA", f"{d['tipo']} · {d['cliente_nombre']} · mensaje editado"
              + (f" · {note}" if note else ""))
    return d


def reject(ctx, action_id, note=""):
    """Broker rejects the proposal. Logged with the reason; never exported."""
    d = _find(ctx, action_id)
    d["estado"] = RECHAZADA
    d["mensaje_final"] = None
    d["decision_note"] = note
    d["decided_by"] = DECIDED_BY
    d["ts_decidida"] = now_iso()
    ctx.audit(DECIDED_BY, "RECHAZADA", f"{d['tipo']} · {d['cliente_nombre']}" + (f" · {note}" if note else ""))
    return d


def approved_for_export(ctx):
    """Actions the broker approved or edited — the only ones exported."""
    return [d for d in ctx.state["inbox"] if d["estado"] in EXPORTABLE]


def summary(ctx):
    """Counts by status and by type — for the dashboard and reconciliation."""
    inbox = ctx.state["inbox"]
    by_estado, by_tipo = {}, {}
    for d in inbox:
        by_estado[d["estado"]] = by_estado.get(d["estado"], 0) + 1
        by_tipo[d["tipo"]] = by_tipo.get(d["tipo"], 0) + 1
    return {"total": len(inbox), "by_estado": by_estado, "by_tipo": by_tipo,
            "pendientes": len(pending(ctx)), "exportables": len(approved_for_export(ctx))}


def sort_key(d):
    """Prioritize the inbox: by severity, then highest confidence, then client."""
    return (SEVERITY_ORDER.get(d["severidad"], 9), -d["confianza"], d["cliente_id"])


def prioritized(ctx):
    """The whole inbox, ordered for the broker to triage top-down."""
    return sorted(ctx.state["inbox"], key=sort_key)
