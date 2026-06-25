"""
nexo_orchestrator.py - The Nexo orchestrator.

Mirrors cfo-office/cfo_orchestrator.py for the broker domain:

  load cartera -> run the 5 agents over a shared CarteraContext -> deterministic
  cross-checks (the inbox reconciles with the detector counts) -> consolidate one
  prioritized inbox (by severity then confidence) -> audit trail -> HITL gate
  (the broker approves) -> export the approved actions.

The agents put their structured results + flags into the shared state; the
orchestrator consumes them, proves the numbers reconcile, and gates output behind
human approval. Numbers come from cartera_core; the model only narrates.

  python nexo/nexo_orchestrator.py                       # interactive HITL gate
  NEXO_AUTO_APPROVE=1 python nexo/nexo_orchestrator.py   # auto-approve (CI/replay)
  NEXO_USE_LLM=1 python nexo/nexo_orchestrator.py        # use Claude for the prose

Requires ANTHROPIC_API_KEY in the repo-root .env only when NEXO_USE_LLM=1.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cartera_core as cc
import review
from shared_state import CarteraContext
import analisis_cartera_agent
import renovaciones_agent
import cobranza_agent
import reactivacion_agent
import cross_sell_agent

# Renewal window and the cross-sell inbox cap (cross-sell openings are abundant;
# the cap keeps the inbox usable and is logged, never silent). Overridable by env.
RENEW_DAYS = int(os.environ.get("NEXO_RENEW_DAYS", "30"))
INACTIVE_MONTHS = int(os.environ.get("NEXO_INACTIVE_MONTHS", "6"))
CROSS_SELL_LIMIT = int(os.environ.get("NEXO_CROSSSELL_LIMIT", "20"))


def _auto() -> bool:
    """Auto-approve when explicitly enabled or when there is no human at the
    console (CI, pipes, replay), so the run never hangs."""
    if os.environ.get("NEXO_AUTO_APPROVE") == "1":
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


# --------------------------------------------------------------------------
# Deterministic cross-checks: the inbox must reconcile with the detectors.
# Same purpose as the CFO orchestrator's cross_checks - if an agent drifts from
# the single source of numbers, it surfaces here instead of in the output.
# --------------------------------------------------------------------------

def cross_checks(ctx, cart):
    issues = []

    # 1) every proposed action of a type matches that agent's reported "propuestos",
    #    and the inbox holds exactly those.
    for agente, tipo in [("renovaciones_agent", "renovacion"),
                         ("cobranza_agent", "cobranza"),
                         ("reactivacion_agent", "reactivacion"),
                         ("cross_sell_agent", "cross_sell")]:
        propuestos = ctx.get(agente, "propuestos", None)
        en_inbox = len(review.list_actions(ctx, tipo=tipo))
        if propuestos is None:
            issues.append(f"{agente}: no reportó 'propuestos'")
        elif propuestos != en_inbox:
            issues.append(f"{agente}: propuestos {propuestos} != inbox {en_inbox}")

    # 2) proposed never exceeds detected (a cap only reduces).
    for agente in ("renovaciones_agent", "cobranza_agent", "reactivacion_agent", "cross_sell_agent"):
        det = ctx.get(agente, "detectados", 0)
        pro = ctx.get(agente, "propuestos", 0)
        if pro > det:
            issues.append(f"{agente}: propuestos {pro} > detectados {det} (imposible)")

    # 3) cobranza mora buckets reconcile to its detected total.
    por_bucket = ctx.get("cobranza_agent", "por_bucket", {})
    det_cob = ctx.get("cobranza_agent", "detectados", 0)
    if sum(por_bucket.values()) != det_cob:
        issues.append(f"cobranza: buckets {sum(por_bucket.values())} != detectados {det_cob}")

    # 4) the analisis metrics agree with the detectors (shared source of numbers):
    m = ctx.get("analisis_cartera_agent", "metrics", {})
    if m:
        if m.get("polizas_en_mora") != det_cob:
            issues.append(f"métricas en_mora {m.get('polizas_en_mora')} != cobranza {det_cob}")
        # renovaciones over the same 30-day window equals metrics' upcoming renewals
        if RENEW_DAYS == m.get("vencimientos_dias") and \
                m.get("vencimientos_proximos") != ctx.get("renovaciones_agent", "detectados"):
            issues.append("métricas vencimientos != renovaciones detectados")
        if m.get("total_polizas") != len(cart.policies):
            issues.append("métricas total_polizas != cartera")

    return issues


def _maybe_export(ctx):
    """Export approved/edited actions to Excel (Phase 5 writer). Wired so the
    orchestrator runs before the writer exists and just works once it does."""
    try:
        from outputs import excel_writer
    except Exception:
        ctx.audit("export", "skip", "writer no disponible (se agrega en la Fase 5)")
        return None
    path = excel_writer.export(ctx)
    ctx.audit("export", "ok", os.path.basename(path))
    return path


# --------------------------------------------------------------------------
# HITL gate.
# --------------------------------------------------------------------------

def hitl_gate(ctx):
    """The human-in-the-loop gate. Auto mode approves every pending action
    (recorded as 'auto', never as a human sign-off). Interactive mode asks the
    broker for a single approve-all / abort decision; granular approve/edit/reject
    per action lives in the Streamlit app."""
    pend = review.pending(ctx)
    if not pend:
        ctx.audit("HITL", "vacío", "no hay acciones pendientes")
        return
    if _auto():
        for d in list(pend):
            review.approve(ctx, d["id"], note="auto-aprobada (NEXO_AUTO_APPROVE)", by="auto")
        ctx.audit("HITL", "AUTO",
                  f"{len(pend)} acciones auto-aprobadas para exportar "
                  "(modo CI/replay; NO es una firma humana)")
        return
    print(f"\n  [human-in-the-loop] Hay {len(pend)} acciones propuestas, pendientes de tu aprobación.")
    print("  (La aprobación/edición/rechazo por acción se hace en la app Streamlit;"
          " acá decidís el lote completo.)")
    try:
        resp = input(f"  ¿Aprobar las {len(pend)} acciones para exportar? [s/N]: ").strip().lower()
    except EOFError:
        resp = "n"
    if resp == "s":
        for d in list(pend):
            review.approve(ctx, d["id"], note="aprobada en lote (CLI)")
        ctx.audit("HITL", "aprobado", f"el productor aprobó {len(pend)} acciones")
    else:
        ctx.audit("HITL", "PENDIENTE",
                  "el productor no aprobó en lote; las acciones quedan pendientes para la app")


# --------------------------------------------------------------------------
# Pipeline.
# --------------------------------------------------------------------------

def build_inbox(cart, ctx=None):
    """Run the 5 agents over the shared state and reconcile. Returns (ctx, issues).

    This is the part the Streamlit app reuses: it builds a PENDING inbox without
    touching the HITL gate, so the app can gate per action via buttons."""
    ctx = ctx or CarteraContext(fresh_audit=True)
    ctx.audit("orchestrator", "start",
              f"{len(cart.policies)} pólizas, {len(cart.by_client())} clientes")

    # Analisis first: it computes the portfolio metrics the cross-checks reconcile
    # against and feeds the dashboard.
    analisis_cartera_agent.run(cart, ctx)
    renovaciones_agent.run(cart, ctx, days=RENEW_DAYS)
    cobranza_agent.run(cart, ctx)
    reactivacion_agent.run(cart, ctx, months=INACTIVE_MONTHS)
    cross_sell_agent.run(cart, ctx, limit=CROSS_SELL_LIMIT)

    # Deterministic integrity: the inbox reconciles with the detectors.
    issues = cross_checks(ctx, cart)
    if issues:
        for i in issues:
            ctx.audit("cross_check", "FAIL", i)
        ctx.put("orchestrator", {"status": "halted_inconsistent", "issues": issues})
    else:
        ctx.audit("cross_check", "ok", "el inbox reconcilia con los detectores")
    return ctx, issues


def run(cartera_path=None, ctx=None):
    cart = cc.load_cartera(cartera_path)
    n_clients = len(cart.by_client())
    print("=" * 64)
    print(f"NEXO · co-piloto del productor de seguros | {len(cart.policies)} pólizas · {n_clients} clientes")
    print("=" * 64)

    ctx = ctx or CarteraContext(fresh_audit=True)
    ctx, issues = build_inbox(cart, ctx)
    if issues:
        ctx.save()
        print("\n  Pipeline detenido: el inbox no reconcilia con los detectores.")
        for i in issues:
            print("   -", i)
        return ctx

    # Consolidate into one prioritized inbox (severity, then confidence).
    inbox = review.prioritized(ctx)
    s = review.summary(ctx)
    print(f"\n  Inbox consolidado: {s['total']} acciones · por tipo {s['by_tipo']}")
    print("  Top prioridad:")
    for d in inbox[:5]:
        print(f"   [{d['severidad']:5}] conf {d['confianza']:.0%} · {d['tipo']:12} · "
              f"{d['cliente_nombre']} · {d['detalle']}")

    # HITL gate, then export only what was approved/edited.
    hitl_gate(ctx)
    path = _maybe_export(ctx)

    ctx.put("orchestrator", {"status": "done", "inbox": s, "export": path})
    saved = ctx.save()
    print(f"\n  Aprobadas/editadas para exportar: {len(review.approved_for_export(ctx))}"
          f" · pendientes: {len(review.pending(ctx))}")
    if path:
        print(f"  Excel de acciones aprobadas: {path}")
    print(f"  Estado guardado en: {os.path.basename(saved)} "
          f"({len(ctx.state['audit'])} eventos de auditoría)")
    return ctx


if __name__ == "__main__":
    run()
