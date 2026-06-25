"""
test_cartera_core.py - Phase 1 tests for the deterministic data layer.

Checks invariants (not hard-coded counts, which would be brittle): the mora
buckets reconcile to the total en mora, the detector outputs are internally
consistent, every detector returns the expected shape, and each one finds at
least one candidate in the demo cartera (so the orchestrator can exercise all
five agents). Pure-Python, no API key, no network.

Run:  python -m pytest nexo/tests/test_cartera_core.py
  or: python nexo/tests/test_cartera_core.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
sys.path.insert(0, NEXO)

import cartera_core as cc
import schema
from data import generate_synthetic_cartera as gen


def _cartera():
    """A Cartera built in-memory from the generator (no file I/O needed)."""
    return cc.Cartera(gen.build(seed=42))


CART = _cartera()


# -- shapes ----------------------------------------------------------------

def test_load_cartera_demo_file_matches_generator():
    """The committed demo file loads and has the same policy count as build()."""
    cart = cc.load_cartera()
    assert len(cart.policies) == len(CART.policies)
    assert len(cart.policies) > 0


def test_policies_have_required_fields():
    for p in CART.policies:
        assert p.cliente_id and p.numero_poliza
        assert p.ramo in schema.RAMOS
        assert p.aseguradora in schema.ASEGURADORAS
        assert p.estado_pago in schema.ESTADO_PAGO
        assert p.estado_poliza in schema.ESTADO_POLIZA
        assert isinstance(p.prima_mensual, float) and p.prima_mensual > 0
        assert 0.0 <= p.comision_pct <= 1.0


def test_numero_poliza_unique():
    nums = [p.numero_poliza for p in CART.policies]
    assert len(nums) == len(set(nums))


# -- detector 1: renovaciones ---------------------------------------------

def test_policies_expiring_shape_and_window():
    rows = CART.policies_expiring(days=30)
    assert len(rows) > 0
    for r in rows:
        assert 0 <= r["dias_para_vencer"] <= 30
        assert set(["cliente_id", "numero_poliza", "ramo", "prima_mensual",
                    "dias_para_vencer"]).issubset(r.keys())
    # sorted soonest-first
    dias = [r["dias_para_vencer"] for r in rows]
    assert dias == sorted(dias)


def test_expiring_window_is_monotonic():
    """A wider window can only include more (or equal) policies."""
    n30 = len(CART.policies_expiring(days=30))
    n60 = len(CART.policies_expiring(days=60))
    assert n60 >= n30


def test_expiring_only_active():
    for r in CART.policies_expiring(days=60):
        pol = next(p for p in CART.policies if p.numero_poliza == r["numero_poliza"])
        assert pol.estado_poliza == "activa"


# -- detector 2: reactivacion ---------------------------------------------

def test_inactive_clients_shape_and_rule():
    rows = CART.inactive_clients(months=6)
    assert len(rows) > 0
    by_client = CART.by_client()
    for r in rows:
        pols = by_client[r["cliente_id"]]
        # no active policy and lapsed beyond the horizon
        assert all(p.estado_poliza != "activa" for p in pols)
        assert r["dias_inactivo"] > 6 * 30
        assert set(["cliente_id", "nombre", "n_polizas", "dias_inactivo"]).issubset(r.keys())


def test_inactive_horizon_is_monotonic():
    """A longer inactivity horizon can only select fewer (or equal) clients."""
    n6 = len(CART.inactive_clients(months=6))
    n12 = len(CART.inactive_clients(months=12))
    assert n12 <= n6


# -- detector 3: cross-sell -----------------------------------------------

def test_cross_sell_shape_and_rule():
    rows = CART.cross_sell_candidates()
    assert len(rows) > 0
    by_client = CART.by_client()
    for r in rows:
        pols = by_client[r["cliente_id"]]
        active_ramos = {p.ramo for p in pols if p.estado_poliza == "activa"}
        held_ramos = {p.ramo for p in pols}
        assert r["has_ramo"] in active_ramos          # holds the base ramo (active)
        assert r["missing_ramo"] not in held_ramos    # truly missing
        assert 0.0 <= r["strength"] <= 1.0


def test_cross_sell_one_per_client_and_missing():
    rows = CART.cross_sell_candidates()
    keys = [(r["cliente_id"], r["missing_ramo"]) for r in rows]
    assert len(keys) == len(set(keys))


# -- detector 4: cobranza (the headline reconciliation) -------------------

def test_mora_buckets_reconcile_to_total():
    mb = CART.mora_buckets()
    # counts: sum of buckets == total en mora
    assert sum(b["count"] for b in mb["buckets"].values()) == mb["total_count"]
    assert mb["total_count"] == len(mb["items"])
    # primas: sum of bucket primas == total prima en mora (within rounding)
    bucket_prima = sum(b["prima_mensual"] for b in mb["buckets"].values())
    assert abs(bucket_prima - mb["total_prima_mensual"]) < 0.01
    # every bucket present and every item lands in exactly its computed bucket
    assert set(mb["buckets"].keys()) == set(schema.MORA_BUCKETS)
    for it in mb["items"]:
        assert it["bucket"] == cc.mora_bucket(it["dias_mora"])


def test_mora_only_active_en_mora():
    mb = CART.mora_buckets()
    nums = {it["numero_poliza"] for it in mb["items"]}
    for p in CART.policies:
        if p.numero_poliza in nums:
            assert p.estado_poliza == "activa" and p.estado_pago == "en_mora"
    # the demo plants policies in all four buckets
    for b in schema.MORA_BUCKETS:
        assert mb["buckets"][b]["count"] > 0


# -- detector 5: portfolio metrics ----------------------------------------

def test_portfolio_metrics_consistency():
    m = CART.portfolio_metrics()
    assert m["total_polizas"] == len(CART.policies)
    # active/expired/cancelled partition the portfolio
    assert (m["polizas_activas"] + m["polizas_vencidas"]
            + m["polizas_canceladas"] == m["total_polizas"])
    # clients partition into active vs inactive
    assert m["clientes_activos"] + m["clientes_inactivos"] == m["total_clientes"]
    # mix sums reconcile to active policy count
    assert sum(v["count"] for v in m["mix_por_ramo"].values()) == m["polizas_activas"]
    assert sum(v["count"] for v in m["mix_por_aseguradora"].values()) == m["polizas_activas"]
    # mora share of active policies matches the cobranza detector
    mb = CART.mora_buckets()
    assert mb["total_count"] <= m["polizas_activas"]
    assert 0.0 <= m["pct_en_mora_polizas"] <= 100.0
    assert 0.0 <= m["retencion_pct"] <= 100.0


def test_metrics_vencimientos_match_detector():
    m = CART.portfolio_metrics(expiring_days=30)
    assert m["vencimientos_proximos"] == len(CART.policies_expiring(days=30))


# -- confidence score ------------------------------------------------------

def test_confidence_is_deterministic_and_bounded():
    a = cc.confidence(1.0, 0.9)
    b = cc.confidence(1.0, 0.9)
    assert a == b                      # deterministic
    assert 0.0 <= a <= 1.0
    # more complete data and a stronger rule never lower confidence
    assert cc.confidence(1.0, 0.9) >= cc.confidence(0.5, 0.9)
    assert cc.confidence(0.8, 0.9) >= cc.confidence(0.8, 0.4)


def test_data_completeness_fraction():
    assert cc.data_completeness(["a", "b"]) == 1.0
    assert cc.data_completeness([None, "b"]) == 0.5
    assert cc.data_completeness([None, None]) == 0.0


# -- determinism of the generator -----------------------------------------

def test_generator_is_deterministic():
    a = [p.to_row() for p in gen.build(seed=42)]
    b = [p.to_row() for p in gen.build(seed=42)]
    assert a == b


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
