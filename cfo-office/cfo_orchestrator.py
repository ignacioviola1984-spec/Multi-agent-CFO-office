"""
cfo_orchestrator.py - El CFO del office.

Corre el cierre como un MODELO OPERATIVO por ETAPAS (ver stages.py) con control
de dos niveles en cada etapa:
  - cada etapa pasa un CONTROL deterministico (codigo) Y la firma del experto de
    dominio de esa funcion (maker-checker / primera linea); si no pasa, entra en
    REWORK y, si no se resuelve, BLOQUEA todo el cierre;
  - recien con TODAS las etapas pasadas, el CFO da la firma FINAL sobre lo
    consolidado + lo material (no re-revisa el detalle que no domina).

Etapas (de punta a punta): Controllership -> Treasury -> Working capital & tax
(AR/AP/Tax) -> Close & financial statements -> FP&A -> Strategic Finance ->
Internal Controls -> Audit -> [cross-checks globales] -> gate FINAL del CFO ->
board pack + acciones.

Cada agente deja su analisis y sus flags en el estado compartido; el CFO los
consume. Los numeros los calculan los agentes por codigo (finance_core); el
modelo solo redacta. Una sola fuente de verdad, todo auditable. Persiste a
cfo_state.json.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python cfo_orchestrator.py
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # finance_core
sys.path.insert(0, HERE)                                  # shared_state + agentes

import finance_core as fc
from shared_state import CFOContext
import review
import stages
import cfo_o2c_bridge   # runs the Order-to-Cash control tower as a sub-orchestration

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIOD = "2026-05"
# Administration y Accounting & Reporting entran como un solo reporte cada uno:
# ya consolidan sus sub-agentes adentro (AR/AP/Tax y Close/Reporting). Audit es
# independiente (tercera linea) y entra como par de los demas.
AGENTS = ["Controller", "Treasury", "Administration", "Accounting & Reporting",
          "FP&A", "Strategic Finance", "Internal Controls", "Audit", "Order-to-Cash"]


def agent(system, prompt, max_tokens=700):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# --- Checks deterministicos de coherencia entre agentes ----------------

def cross_checks(ctx):
    """Verifica que los agentes concuerden en los numeros compartidos.

    Como todos derivan de finance_core, deben coincidir; este check prueba que
    el pipeline esta bien cableado y atrapa derivas futuras (si un agente
    cambia su calculo y deja de concordar, salta aca y no en el board).
    """
    issues = []
    ctrl, trez, fpa = ctx.get("Controller"), ctx.get("Treasury"), ctx.get("FP&A")
    strat = ctx.get("Strategic Finance")

    # 1) op income del Controller == actual de operating income en la varianza de FP&A
    try:
        oi_ctrl = ctrl["pnl"]["operating_income"]
        oi_fpa = next(v for v in fpa["budget_variance"]["rows"]
                      if v["label"] == "Operating income")["actual"]
        if abs(oi_ctrl - oi_fpa) > 1:
            issues.append(f"op income mismatch: Controller {oi_ctrl:,.0f} vs FP&A {oi_fpa:,.0f}")
    except (KeyError, TypeError, StopIteration):
        issues.append("missing data to reconcile op income between Controller and FP&A")

    # 2) burn de Treasury == -op income (cuando hay perdida operativa)
    try:
        oi_ctrl = ctrl["pnl"]["operating_income"]
        expected_burn = -oi_ctrl if oi_ctrl < 0 else 0.0
        if abs(trez["burn"] - expected_burn) > 1:
            issues.append(f"Treasury burn {trez['burn']:,.0f} != -op income {expected_burn:,.0f}")
    except (KeyError, TypeError):
        issues.append("missing data to reconcile Treasury burn")

    # 3) el run-rate de Strategic (revenue mensual x 12) debe atarse al revenue del Controller
    try:
        rev_ctrl = ctrl["pnl"]["revenue"]
        rev_strat = strat["metrics"]["run_rate"] / 12.0
        if abs(rev_ctrl - rev_strat) > 1:
            issues.append(f"revenue mismatch: Controller {rev_ctrl:,.0f} vs Strategic {rev_strat:,.0f}")
    except (KeyError, TypeError, ZeroDivisionError):
        issues.append("missing data to reconcile revenue with Strategic Finance")

    # 4) el AR del agente de Administracion debe atarse al AR del Controller
    try:
        ar_admin = ctx.get("Accounts Receivable")["metrics"]["total"]
        ar_ctrl = ctrl["ar"]["total"]
        if abs(ar_admin - ar_ctrl) > 1:
            issues.append(f"AR mismatch: Controller {ar_ctrl:,.0f} vs AR agent {ar_admin:,.0f}")
    except (KeyError, TypeError):
        issues.append("missing data to reconcile AR with Administration")

    # 5) el resultado neto de Reporting debe atarse al op income del Controller.
    # Asuncion: en este dataset no hay lineas debajo de la operativa (sin
    # intereses ni impuestos en el P&L), asi que net income == operating income.
    # Si se agregan esas lineas, recalcular el neto esperado con los mismos
    # drivers en vez de exigir igualdad cruda (si no, este check frenaria el
    # cierre como "inconsistente" estando los libros bien).
    try:
        ni_rep = ctx.get("Financial Reporting")["income_statement"]["net_income"]
        oi_ctrl = ctrl["pnl"]["operating_income"]
        if abs(ni_rep - oi_ctrl) > 1:
            issues.append(f"net income mismatch: Controller {oi_ctrl:,.0f} vs Reporting {ni_rep:,.0f}")
    except (KeyError, TypeError):
        issues.append("missing data to reconcile net income with Financial Reporting")

    # 6) la caja del balance de Reporting debe atarse a la caja de Treasury
    try:
        cash_rep = ctx.get("Financial Reporting")["balance_sheet"]["assets"]["cash"]
        if abs(cash_rep - trez["cash"]) > 1:
            issues.append(f"cash mismatch: Treasury {trez['cash']:,.0f} vs Reporting {cash_rep:,.0f}")
    except (KeyError, TypeError):
        issues.append("missing data to reconcile cash with Financial Reporting")

    return issues


def gather_escalations(ctx):
    """Junta los escalamientos de todos los agentes y los ordena por severidad."""
    esc = []
    for a in AGENTS:
        esc.extend(tuple(e) for e in ctx.get(a, "escalations", []))
    order = {"CRITICAL": 0, "HIGH": 1}
    return sorted(esc, key=lambda e: order.get(e[0], 9))


def cfo_final_gate(ctx, esc):
    """The CFO's FINAL sign-off — the second tier, not a detail review.

    Precondition: every function must already be signed off by its domain expert
    (the first line). The CFO does NOT re-review each operational detail (a
    generalist can't); the CFO confirms the first line cleared and signs off on
    the consolidated board pack and the material / cross-cutting items.
    """
    fl = review.first_line_status(ctx)
    print(f"\n  [CFO final sign-off] First line: {len(fl['approved'])}/{fl['total']} "
          "functions signed off by their domain experts.")
    if not fl["all_approved"]:
        print("   NOT cleared by their reviewers (must resolve before the CFO can sign): "
              + ", ".join(fl["rejected"]))
        return False
    serious = [e for e in esc if e[0] in ("HIGH", "CRITICAL")]
    if serious:
        print("   Material / cross-cutting items for the CFO:")
        for sev, msg in serious:
            print(f"     - [{sev}] {msg}")
    if review._auto():
        return True
    try:
        return input("  CFO — approve the consolidated board pack? [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


def compose_board_pack(ctx):
    ctrl = ctx.get("Controller", "narrative", "")
    trez = ctx.get("Treasury", "narrative", "")
    fpa = ctx.get("FP&A")
    fpa_bits = "\n".join(filter(None, [
        fpa.get("variance_expl", ""), fpa.get("budget_expl", ""), fpa.get("anomaly_expl", ""),
    ]))
    strat = ctx.get("Strategic Finance", "narrative", "")
    admin = ctx.get("Administration", "narrative", "")
    acctrep = ctx.get("Accounting & Reporting", "narrative", "")
    controls = ctx.get("Internal Controls", "narrative", "")
    audit = ctx.get("Audit", "narrative", "")
    prompt = (
        f"--- Controller (close) ---\n{ctrl}\n\n"
        f"--- Treasury (liquidity) ---\n{trez}\n\n"
        f"--- Administration (AR / AP / Tax) ---\n{admin}\n\n"
        f"--- Accounting & Reporting (close + financial statements) ---\n{acctrep}\n\n"
        f"--- FP&A (MoM variance, budget variance, anomalies) ---\n{fpa_bits}\n\n"
        f"--- Strategic Finance (growth, efficiency, path to breakeven) ---\n{strat}\n\n"
        f"--- Internal Controls (assurance) ---\n{controls}\n\n"
        f"--- Audit (independent opinion) ---\n{audit}\n\n"
        "Write the consolidated board pack for the period."
    )
    return agent(
        "You are the CFO. With the inputs from Controller, Treasury, Administration, Accounting & "
        "Reporting, FP&A, Strategic Finance, Internal Controls and Audit, write an executive board "
        "pack of 6-8 sentences, CFO tone, direct, no filler. Do not add new numbers. Write in English.",
        prompt,
    )


def compose_actions(ctx):
    fpa = ctx.get("FP&A")
    esc = gather_escalations(ctx)
    esc_txt = "\n".join(f"  - [{s}] {m}" for s, m in esc) or "  (no escalations)"
    prompt = (
        f"Escalations for the period:\n{esc_txt}\n\n"
        f"FP&A findings:\n{fpa.get('budget_expl', '')}\n{fpa.get('anomaly_expl', '')}\n\n"
        "Propose 3 prioritized actions, one line each."
    )
    return agent(
        "You are the CFO. Propose 3 concrete, actionable, prioritized actions from the "
        "escalations and findings. One line each. Do not add new numbers; use only the figures "
        "in the escalations and findings given. Write in English.",
        prompt,
    )


# --- Pipeline del office -----------------------------------------------

def run(period=PERIOD):
    print("=" * 60)
    print(f"CFO OFFICE | close {period}")
    print("=" * 60)
    ctx = CFOContext()
    ctx.audit("CFO", "start", f"running the operating model for {period}")

    # Run the operating model stage by stage. Each stage = agent(s) + a
    # deterministic control + the domain expert's sign-off, with rework and a
    # hard block if it cannot pass (stages.run_all).
    stage_results, all_passed = stages.run_all(ctx)
    if not all_passed:
        blocked = next((s for s in stage_results if s["status"] == "blocked"), None)
        ctx.put("CFO", {"status": "blocked_stage",
                        "blocked_stage": blocked["name"] if blocked else None})
        ctx.save()
        print(f"\n  Operating model halted at stage "
              f"{blocked['id']} ({blocked['name']}): {blocked['reason']}" if blocked
              else "\n  Operating model halted.")
        return ctx

    # Cross-checks between agents (global integrity, before escalating or writing).
    issues = cross_checks(ctx)
    if issues:
        for i in issues:
            ctx.audit("cross_check", "FAIL", i)
        print("\n  Pipeline stopped: the agents don't agree on the numbers.")
        ctx.put("CFO", {"status": "halted_inconsistent"})
        ctx.save()
        return ctx
    ctx.audit("cross_check", "ok", "agents consistent on the shared numbers")

    # Sub-orchestration: the Order-to-Cash control tower runs UNDER the CFO. It is
    # deterministic (no LLM), writes its result into the same shared state, and its
    # escalations are consolidated with the close. Wrapped so it can never break
    # the close.
    try:
        o2c = cfo_o2c_bridge.run_o2c_suborchestration(ctx, period)
        print(f"\n  [Order-to-Cash] {o2c['status']} | {o2c['hard_failures']} hard fail | "
              f"audit {o2c['audit_opinion'].upper()}")
    except Exception as e:                              # pragma: no cover
        ctx.audit("Order-to-Cash", "ERROR", f"O2C sub-orchestration failed: {e}")

    # Every stage passed its control + first-line sign-off (recorded by stages).
    ctx.put("CFO", {"first_line": review.first_line_status(ctx)})

    # Consolidate escalations from all agents (now incl. Order-to-Cash).
    esc = gather_escalations(ctx)
    for sev, msg in esc:
        ctx.audit("escalation", sev, msg)

    # Second tier: the CFO's final sign-off on the consolidated pack + material items.
    if not cfo_final_gate(ctx, esc):
        ctx.put("CFO", {"status": "rejected"})
        ctx.audit("CFO", "REJECTED", "CFO did not approve; board pack not fixed")
        ctx.save()
        print("\n  Stopped by CFO decision.")
        return ctx
    ctx.audit("CFO", "approved", "CFO signed off the consolidated board pack")

    board = compose_board_pack(ctx)
    # Append the deterministic Order-to-Cash section so its numbers come from
    # o2c_core, not from the LLM narration.
    o2c_section = ctx.get("Order-to-Cash", "section", "")
    if o2c_section:
        board = board + "\n\n" + o2c_section
    actions = compose_actions(ctx)
    ctx.put("CFO", {"board_pack": board, "actions": actions, "status": "approved"})
    ctx.audit("CFO", "ok", "consolidated board pack and actions fixed")

    path = ctx.save()   # persist state BEFORE displaying it
    print("\n--- BOARD PACK (CFO) ---\n" + board)
    print("\n--- PROPOSED ACTIONS ---\n" + actions)
    print(f"\nShared state saved to: {os.path.basename(path)} "
          f"({len(ctx.state['audit'])} audit events)")
    return ctx


if __name__ == "__main__":
    run()
