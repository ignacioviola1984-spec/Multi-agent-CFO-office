"""
test_spine.py - Phase 2 tests for the shared state + the single-broker inbox.

Exercises CarteraContext (put/get/flag/audit/save/load) and review.py (the
pendiente -> aprobada/editada/rechazada machine, export filtering, prioritization,
and the who/what/when record). Uses temp paths so it never writes real run
artifacts. No API key, no network.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
sys.path.insert(0, NEXO)

import shared_state
import review
from shared_state import CarteraContext
from review import Action


def _ctx(tmp_path):
    return CarteraContext(state_path=str(tmp_path / "state.json"),
                          audit_path=str(tmp_path / "audit.jsonl"),
                          fresh_audit=True)


def _action(i=1, tipo="renovacion", conf=0.8, sev="MEDIA"):
    return Action(
        id=review.make_id(tipo, f"CLI-{i:04d}", "POL-1"),
        tipo=tipo, agente="renovaciones_agent",
        cliente_id=f"CLI-{i:04d}", cliente_nombre=f"Cliente {i}",
        detalle="Auto vence en 12 dias", confianza=conf, severidad=sev,
        datos={"dias_para_vencer": 12, "prima_mensual": 50000.0},
        mensaje_propuesto="Hola, te recuerdo la renovacion.", poliza="POL-1")


# -- CarteraContext --------------------------------------------------------

def test_put_get_and_flags(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.put("analisis_cartera_agent", {"metrics": {"total_polizas": 212}})
    assert ctx.get("analisis_cartera_agent", "metrics")["total_polizas"] == 212
    assert ctx.get("missing", "x", default="d") == "d"
    ctx.flag("cobranza_agent", "ALTA", "11 polizas en mora 90+")
    assert ctx.state["flags"][0]["severity"] == "ALTA"
    # flag is also written to the audit trail
    assert any(e["status"] == "FLAG/ALTA" for e in ctx.state["audit"])


def test_audit_writes_jsonl(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.audit("x", "ok", "hello")
    with open(ctx.audit_path, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) == 1 and "hello" in lines[0]


def test_save_and_load_roundtrip(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.put("a", {"k": 1})
    review.add_action(ctx, _action())
    path = ctx.save()
    assert os.path.exists(path)
    reloaded = CarteraContext.load(state_path=path)
    assert reloaded.get("a", "k") == 1
    assert len(reloaded.state["inbox"]) == 1


# -- Action dataclass ------------------------------------------------------

def test_action_roundtrip():
    a = _action()
    d = a.to_dict()
    b = Action.from_dict(d)
    assert b == a
    assert b.estado == review.PENDIENTE


def test_make_id_is_stable():
    assert review.make_id("cobranza", "CLI-0007", "POL-9") == "cobranza|CLI-0007|POL-9"


# -- the inbox state machine ----------------------------------------------

def test_add_starts_pendiente(tmp_path):
    ctx = _ctx(tmp_path)
    review.add_action(ctx, _action())
    assert len(review.pending(ctx)) == 1
    assert review.pending(ctx)[0]["estado"] == review.PENDIENTE


def test_approve_sets_final_message_and_who_when(tmp_path):
    ctx = _ctx(tmp_path)
    aid = review.add_action(ctx, _action())
    d = review.approve(ctx, aid, note="ok dale")
    assert d["estado"] == review.APROBADA
    assert d["mensaje_final"] == d["mensaje_propuesto"]   # approved as-is
    assert d["decided_by"] == review.DECIDED_BY
    assert d["ts_decidida"] and d["decision_note"] == "ok dale"
    assert review.pending(ctx) == []


def test_edit_changes_final_message(tmp_path):
    ctx = _ctx(tmp_path)
    aid = review.add_action(ctx, _action())
    d = review.edit(ctx, aid, "Mensaje reescrito por el productor.", note="mas corto")
    assert d["estado"] == review.EDITADA
    assert d["mensaje_final"] == "Mensaje reescrito por el productor."
    assert d["mensaje_final"] != d["mensaje_propuesto"]


def test_reject_is_logged_and_not_exported(tmp_path):
    ctx = _ctx(tmp_path)
    aid = review.add_action(ctx, _action())
    d = review.reject(ctx, aid, note="cliente pidio no contactar")
    assert d["estado"] == review.RECHAZADA
    assert d["mensaje_final"] is None
    assert d not in review.approved_for_export(ctx)
    # the rejection (with reason) is in the audit trail
    assert any(e["status"] == "RECHAZADA" and "no contactar" in e["detail"]
               for e in ctx.state["audit"])


def test_only_approved_or_edited_are_exported(tmp_path):
    ctx = _ctx(tmp_path)
    a1 = review.add_action(ctx, _action(1))
    a2 = review.add_action(ctx, _action(2))
    a3 = review.add_action(ctx, _action(3))
    a4 = review.add_action(ctx, _action(4))
    review.approve(ctx, a1)
    review.edit(ctx, a2, "editado")
    review.reject(ctx, a3)
    # a4 stays pendiente
    exported = review.approved_for_export(ctx)
    ids = {d["id"] for d in exported}
    assert ids == {a1, a2}
    s = review.summary(ctx)
    assert s["total"] == 4 and s["exportables"] == 2 and s["pendientes"] == 1
    assert s["by_estado"][review.RECHAZADA] == 1


def test_prioritized_orders_by_severity_then_confidence(tmp_path):
    ctx = _ctx(tmp_path)
    review.add_action(ctx, _action(1, conf=0.9, sev="BAJA"))
    review.add_action(ctx, _action(2, conf=0.5, sev="ALTA"))
    review.add_action(ctx, _action(3, conf=0.95, sev="ALTA"))
    order = [d["cliente_id"] for d in review.prioritized(ctx)]
    # ALTA first, and within ALTA the higher confidence first
    assert order[0] == "CLI-0003"   # ALTA, 0.95
    assert order[1] == "CLI-0002"   # ALTA, 0.50
    assert order[2] == "CLI-0001"   # BAJA


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
