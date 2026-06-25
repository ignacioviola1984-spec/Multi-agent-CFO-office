"""
test_orchestrator.py - Phase 4 tests for the orchestrator.

Runs the full loop in auto-approve mode over a hermetic context and checks: the
cross-checks reconcile the inbox with the detectors, the inbox consolidates and
prioritizes correctly, auto-approval is recorded as 'auto' (never a human
sign-off), and a tampered context is caught by the cross-checks. No API key.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
sys.path.insert(0, NEXO)

import cartera_core as cc
import review
import nexo_orchestrator as orch
from shared_state import CarteraContext


def _run(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_AUTO_APPROVE", "1")
    ctx = CarteraContext(state_path=str(tmp_path / "s.json"),
                         audit_path=str(tmp_path / "a.jsonl"), fresh_audit=True)
    return orch.run(ctx=ctx)


def test_full_loop_auto_approves_and_reconciles(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    # the run did not halt
    assert ctx.get("orchestrator", "status") == "done"
    # cross-checks are clean on a fresh run
    assert orch.cross_checks(ctx, cc.load_cartera()) == []
    # everything approved, nothing left pending
    assert review.pending(ctx) == []
    assert len(review.approved_for_export(ctx)) == len(ctx.state["inbox"])


def test_inbox_reconciles_with_agent_counts(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    for agente, tipo in [("renovaciones_agent", "renovacion"),
                         ("cobranza_agent", "cobranza"),
                         ("reactivacion_agent", "reactivacion"),
                         ("cross_sell_agent", "cross_sell")]:
        assert ctx.get(agente, "propuestos") == len(review.list_actions(ctx, tipo=tipo))


def test_cross_sell_capped(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    # cross-sell is capped to the configured limit and detected exceeds it
    assert ctx.get("cross_sell_agent", "propuestos") == orch.CROSS_SELL_LIMIT
    assert ctx.get("cross_sell_agent", "detectados") > orch.CROSS_SELL_LIMIT


def test_auto_approval_is_not_a_human_signoff(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    for d in ctx.state["inbox"]:
        assert d["estado"] == review.APROBADA
        assert d["decided_by"] == "auto"          # not "productor"
        assert "NEXO_AUTO_APPROVE" in d["decision_note"]


def test_prioritized_inbox_is_sorted(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    inbox = review.prioritized(ctx)
    keys = [review.sort_key(d) for d in inbox]
    assert keys == sorted(keys)
    # the most urgent item is an ALTA
    assert inbox[0]["severidad"] == "ALTA"


def test_cross_checks_catch_tampering(tmp_path, monkeypatch):
    ctx = _run(tmp_path, monkeypatch)
    # corrupt an agent's reported count -> cross-checks must flag it
    ctx.state["agents"]["renovaciones_agent"]["propuestos"] += 5
    issues = orch.cross_checks(ctx, cc.load_cartera())
    assert any("renovaciones_agent" in i for i in issues)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
