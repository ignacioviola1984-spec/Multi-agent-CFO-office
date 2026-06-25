"""
test_excel_writer.py - Phase 5 tests for the Excel export.

Verifies the headline guarantee: ONLY approved/edited actions are exported
(pending and rejected are excluded), the final message rides along, and the
dashboard sheet carries the portfolio metrics. Reads the file back to assert.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
sys.path.insert(0, NEXO)

import pandas as pd

import cartera_core as cc
import review
from shared_state import CarteraContext
from review import Action
from outputs import excel_writer


def _action(i, tipo="renovacion", msg="Hola, te recuerdo la renovacion."):
    return Action(
        id=review.make_id(tipo, f"CLI-{i:04d}", "POL-1"), tipo=tipo,
        agente="renovaciones_agent", cliente_id=f"CLI-{i:04d}",
        cliente_nombre=f"Cliente {i}", detalle="detalle", confianza=0.8,
        severidad="MEDIA", datos={"x": 1}, mensaje_propuesto=msg,
        poliza="POL-1", email=f"c{i}@example.com", telefono="+54 11 1234 5678")


def _ctx_with_metrics(tmp_path):
    ctx = CarteraContext(state_path=str(tmp_path / "s.json"),
                         audit_path=str(tmp_path / "a.jsonl"), fresh_audit=True)
    m = cc.load_cartera().portfolio_metrics()
    ctx.state["metrics"] = m
    ctx.put("analisis_cartera_agent", {"metrics": m, "narrative": "Insight de prueba."})
    return ctx


def test_only_approved_or_edited_exported(tmp_path):
    ctx = _ctx_with_metrics(tmp_path)
    for i in range(1, 5):
        review.add_action(ctx, _action(i))
    review.approve(ctx, review.make_id("renovacion", "CLI-0001", "POL-1"))
    review.edit(ctx, review.make_id("renovacion", "CLI-0002", "POL-1"),
                "Mensaje editado por el productor.")
    review.reject(ctx, review.make_id("renovacion", "CLI-0003", "POL-1"))
    # CLI-0004 stays pendiente

    path = excel_writer.export(ctx, path=str(tmp_path / "out.xlsx"))
    assert os.path.exists(path)

    acc = pd.read_excel(path, sheet_name="acciones_aprobadas", engine="openpyxl")
    ids = set(acc["cliente_id"])
    assert ids == {"CLI-0001", "CLI-0002"}            # rejected + pending excluded
    # the edited row carries the edited text
    edited = acc[acc["cliente_id"] == "CLI-0002"]["mensaje_final"].iloc[0]
    assert edited == "Mensaje editado por el productor."
    # every exported row has a non-empty final message
    assert acc["mensaje_final"].astype(str).str.strip().ne("").all()


def test_sheets_and_dashboard_present(tmp_path):
    ctx = _ctx_with_metrics(tmp_path)
    review.add_action(ctx, _action(1))
    review.approve(ctx, review.make_id("renovacion", "CLI-0001", "POL-1"))
    path = excel_writer.export(ctx, path=str(tmp_path / "out.xlsx"))

    xl = pd.ExcelFile(path, engine="openpyxl")
    assert set(["acciones_aprobadas", "dashboard", "mix_aseguradora", "mix_ramo"]).issubset(xl.sheet_names)
    dash = pd.read_excel(xl, "dashboard")
    metricas = set(dash["metrica"])
    assert "Total de pólizas" in metricas
    assert "Pólizas en mora" in metricas
    # the dashboard reflects the real portfolio total
    total = dash[dash["metrica"] == "Total de pólizas"]["valor"].iloc[0]
    assert int(total) == len(cc.load_cartera().policies)


def test_empty_export_still_writes_file(tmp_path):
    """No approved actions -> the file (and dashboard) is still produced."""
    ctx = _ctx_with_metrics(tmp_path)
    review.add_action(ctx, _action(1))   # left pendiente
    path = excel_writer.export(ctx, path=str(tmp_path / "out.xlsx"))
    acc = pd.read_excel(path, sheet_name="acciones_aprobadas", engine="openpyxl")
    assert len(acc) == 0
    assert "dashboard" in pd.ExcelFile(path, engine="openpyxl").sheet_names


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
