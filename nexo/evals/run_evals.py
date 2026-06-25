"""
run_evals.py - Nexo guardrail + regression evals. Exits non-zero on any failure.

Three suites (mirrors evals/ in the finance repo, for the broker domain):

  (a) grounding   - every proposed message references only figures present in its
                    payload, and only clients/policies that exist in the cartera;
                    plus a positive check that the guard rejects an invented figure.
  (b) determinism - re-running the detectors yields identical numbers, the mora
                    buckets reconcile, the metrics partition, and re-running the
                    whole inbox is byte-identical.
  (c) scope       - each agent's message stays on-topic for its type (and does not
                    drift into another agent's topic).

Run:  python nexo/evals/run_evals.py        (exit 0 = all pass, 1 = failure)
Forced offline (templates) so it is deterministic and needs no API key. The guard
itself is exercised on the LLM-rejection path in tests/test_agents.py.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXO = os.path.dirname(HERE)
if NEXO not in sys.path:
    sys.path.insert(0, NEXO)

# Force offline so the evals are deterministic and key-free.
os.environ["NEXO_USE_LLM"] = "0"

import cartera_core as cc
import llm
import nexo_orchestrator as orch
from shared_state import CarteraContext


def _fresh_ctx():
    import tempfile
    td = tempfile.mkdtemp()
    return CarteraContext(state_path=os.path.join(td, "s.json"),
                          audit_path=os.path.join(td, "a.jsonl"), fresh_audit=True)


def _built_inbox():
    cart = cc.load_cartera()
    ctx, issues = orch.build_inbox(cart, _fresh_ctx())
    return cart, ctx, issues


# --------------------------------------------------------------------------
# (a) Grounding
# --------------------------------------------------------------------------

def eval_grounding():
    fails = []
    cart, ctx, issues = _built_inbox()
    if issues:
        fails.append(f"build_inbox no reconcilió: {issues}")
    client_ids = set(cart.by_client().keys())
    policy_nums = {p.numero_poliza for p in cart.policies}

    for d in ctx.state["inbox"]:
        msg = d["mensaje_propuesto"]
        allowed = d["datos"].get("_allowed_numbers", [])
        ok, off = llm.grounding_ok(msg, allowed)
        if not ok:
            fails.append(f"[{d['id']}] cifra fuera del payload: {off} | msg: {msg!r}")
        if d["cliente_id"] not in client_ids:
            fails.append(f"[{d['id']}] cliente inexistente en la cartera: {d['cliente_id']}")
        if d.get("poliza") and d["poliza"] not in policy_nums:
            fails.append(f"[{d['id']}] póliza inexistente en la cartera: {d['poliza']}")

    # positive control: the guard MUST reject an invented figure
    ok, _ = llm.grounding_ok("Tenés una deuda de 987654 pesos.", [12])
    if ok:
        fails.append("el guard NO rechazó una cifra inventada (987654)")

    return ("grounding", fails)


# --------------------------------------------------------------------------
# (b) Determinism / regression
# --------------------------------------------------------------------------

def eval_determinism():
    fails = []
    a, b = cc.load_cartera(), cc.load_cartera()
    checks = {
        "policies_expiring(30)": (a.policies_expiring(30), b.policies_expiring(30)),
        "inactive_clients(6)": (a.inactive_clients(6), b.inactive_clients(6)),
        "cross_sell_candidates": (a.cross_sell_candidates(), b.cross_sell_candidates()),
        "mora_buckets": (a.mora_buckets(), b.mora_buckets()),
        "portfolio_metrics": (a.portfolio_metrics(), b.portfolio_metrics()),
    }
    for name, (x, y) in checks.items():
        if x != y:
            fails.append(f"detector no determinístico entre dos cargas: {name}")

    # buckets reconcile
    mb = a.mora_buckets()
    if sum(v["count"] for v in mb["buckets"].values()) != mb["total_count"]:
        fails.append("mora: la suma de los buckets != total")
    if abs(sum(v["prima_mensual"] for v in mb["buckets"].values()) - mb["total_prima_mensual"]) > 0.01:
        fails.append("mora: la suma de primas por bucket != prima total")

    # metrics partition + reconcile with detectors
    m = a.portfolio_metrics()
    if m["polizas_activas"] + m["polizas_vencidas"] + m["polizas_canceladas"] != m["total_polizas"]:
        fails.append("métricas: activas+vencidas+canceladas != total")
    if m["clientes_activos"] + m["clientes_inactivos"] != m["total_clientes"]:
        fails.append("métricas: activos+inactivos != total clientes")
    if m["polizas_en_mora"] != mb["total_count"]:
        fails.append("métricas: polizas_en_mora != cobranza detectados")
    if m["vencimientos_proximos"] != len(a.policies_expiring(m["vencimientos_dias"])):
        fails.append("métricas: vencimientos_proximos != detector")

    # whole inbox is byte-identical across runs
    def fingerprint():
        _, ctx, _ = _built_inbox()
        return [(d["id"], d["confianza"], d["severidad"], d["mensaje_propuesto"])
                for d in ctx.state["inbox"]]
    if fingerprint() != fingerprint():
        fails.append("el inbox completo no es idéntico entre dos corridas")

    return ("determinism", fails)


# --------------------------------------------------------------------------
# (c) Scope
# --------------------------------------------------------------------------

# Per-type, case-insensitive stems: the message must contain at least one `any`
# stem and none of the `forbidden` ones (which belong to other agents' topics).
SCOPE = {
    "renovacion":   {"any": ["renov", "venc", "cobertur"], "forbidden": ["atras", "mora", "impaga", "deuda"]},
    "cobranza":     {"any": ["cuota", "atras", "pago", "regulariz"], "forbidden": ["renov", "sumar", "cross"]},
    "reactivacion": {"any": ["volver", "acompañ", "al día", "tiempo"], "forbidden": ["atras", "mora", "venc"]},
    "cross_sell":   {"any": ["sumar", "propuesta", "complement"], "forbidden": ["atras", "mora", "venc"]},
}


def eval_scope():
    fails = []
    _, ctx, _ = _built_inbox()
    for d in ctx.state["inbox"]:
        rules = SCOPE.get(d["tipo"])
        if not rules:
            continue
        msg = d["mensaje_propuesto"].lower()
        if not any(stem in msg for stem in rules["any"]):
            fails.append(f"[{d['id']}] {d['tipo']}: sin término on-topic | {d['mensaje_propuesto']!r}")
        for bad in rules["forbidden"]:
            if bad in msg:
                fails.append(f"[{d['id']}] {d['tipo']}: término fuera de tema '{bad}' | {d['mensaje_propuesto']!r}")
        # cross-sell must name the ramo it is proposing
        if d["tipo"] == "cross_sell":
            ramo = d["datos"].get("missing_ramo", "")
            if ramo and ramo.lower() not in msg:
                fails.append(f"[{d['id']}] cross_sell no menciona el ramo propuesto ({ramo})")
    return ("scope", fails)


def main():
    suites = [eval_grounding(), eval_determinism(), eval_scope()]
    print("=" * 60)
    print("NEXO EVALS")
    print("=" * 60)
    total_fail = 0
    for name, fails in suites:
        status = "PASS" if not fails else f"FAIL ({len(fails)})"
        print(f"  [{status:9}] {name}")
        for f in fails[:10]:
            print(f"      - {f}")
        total_fail += len(fails)
    print("-" * 60)
    if total_fail:
        print(f"RESULTADO: {total_fail} fallo(s). Exit 1.")
        return 1
    print("RESULTADO: todas las evals pasaron. Exit 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
