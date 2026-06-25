"""
test_agents.py - Phase 3 tests for the five agents + the grounding guard.

Runs each agent (offline / template mode, no API key) into a fresh context and
checks: actions enter as pendiente, confidence is deterministic and bounded,
every emitted message is GROUNDED (cites no number outside its allowed payload),
and the analisis agent produces metrics + a grounded narrative with no inbox
action. Also unit-tests that the guard rejects an invented figure.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
sys.path.insert(0, NEXO)

import cartera_core as cc
import llm
import review
from shared_state import CarteraContext
import renovaciones_agent
import cobranza_agent
import reactivacion_agent
import cross_sell_agent
import analisis_cartera_agent

CART = cc.load_cartera()


def _ctx(tmp_path, name="t"):
    return CarteraContext(state_path=str(tmp_path / f"{name}.json"),
                          audit_path=str(tmp_path / f"{name}.jsonl"), fresh_audit=True)


# -- grounding guard (the core guardrail) ----------------------------------

def test_guard_accepts_only_payload_numbers():
    ok, off = llm.grounding_ok("Vence en 12 dias.", [12])
    assert ok and off == []


def test_guard_rejects_invented_number():
    ok, off = llm.grounding_ok("Tenes una deuda de 999999 pesos.", [12])
    assert not ok and "999999" in off


def test_guard_normalizes_thousands_separators():
    # '50.000' and '50,000' must match the payload value 50000
    assert llm.grounding_ok("Son 50.000 pesos.", [50000])[0]
    assert llm.grounding_ok("Son 50,000 pesos.", [50000])[0]


# -- per-agent: pendiente, deterministic confidence, grounded message ------

def _assert_pendiente_and_bounded(actions):
    assert len(actions) > 0
    for a in actions:
        assert a.estado == review.PENDIENTE
        assert 0.0 <= a.confianza <= 1.0
        assert a.severidad in ("ALTA", "MEDIA", "BAJA")
        assert a.mensaje_propuesto.strip()


def test_renovaciones_grounded_and_pendiente(tmp_path):
    acts = renovaciones_agent.run(CART, _ctx(tmp_path, "ren"))
    _assert_pendiente_and_bounded(acts)
    for a in acts:
        # the body may cite only the day count
        ok, off = llm.grounding_ok(a.mensaje_propuesto, [a.datos["dias_para_vencer"]])
        assert ok, (a.mensaje_propuesto, off)


def test_cobranza_grounded_and_bucketed(tmp_path):
    acts = cobranza_agent.run(CART, _ctx(tmp_path, "cob"))
    _assert_pendiente_and_bounded(acts)
    for a in acts:
        ok, off = llm.grounding_ok(a.mensaje_propuesto, [a.datos["dias_mora"]])
        assert ok, (a.mensaje_propuesto, off)
        assert a.datos["bucket"] in ("0-30", "31-60", "61-90", "90+")


def test_reactivacion_message_has_no_numbers(tmp_path):
    acts = reactivacion_agent.run(CART, _ctx(tmp_path, "rea"))
    _assert_pendiente_and_bounded(acts)
    for a in acts:
        # win-back message is qualitative: it must cite no figure at all
        assert llm.numbers_in(a.mensaje_propuesto) == set(), a.mensaje_propuesto


def test_cross_sell_message_has_no_numbers_and_caps(tmp_path):
    ctx = _ctx(tmp_path, "xs")
    acts = cross_sell_agent.run(CART, ctx, limit=10)
    assert len(acts) == 10                       # the cap is honored
    assert any(e["status"] == "CAP" for e in ctx.state["audit"])   # and logged
    for a in acts:
        assert llm.numbers_in(a.mensaje_propuesto) == set(), a.mensaje_propuesto


def test_analisis_has_metrics_grounded_narrative_no_action(tmp_path):
    ctx = _ctx(tmp_path, "ana")
    out = analisis_cartera_agent.run(CART, ctx)
    assert ctx.state["inbox"] == []              # no per-client action
    assert out["metrics"]["total_polizas"] == len(CART.policies)
    facts = analisis_cartera_agent.build_facts(out["metrics"])
    template = analisis_cartera_agent.build_template(out["metrics"])
    ok, off = llm.grounding_ok(out["narrative"], [facts, template])
    assert ok, off


# -- determinism: same inputs -> identical proposals -----------------------

def test_agents_are_deterministic(tmp_path):
    a1 = renovaciones_agent.run(CART, _ctx(tmp_path, "d1"))
    a2 = renovaciones_agent.run(CART, _ctx(tmp_path, "d2"))
    assert [(x.id, x.confianza, x.mensaje_propuesto, x.severidad) for x in a1] == \
           [(x.id, x.confianza, x.mensaje_propuesto, x.severidad) for x in a2]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
