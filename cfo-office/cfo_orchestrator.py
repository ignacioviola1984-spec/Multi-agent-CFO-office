"""
cfo_orchestrator.py - El CFO del office.

Instancia un unico estado compartido (CFOContext), corre a los agentes
especializados sobre ese estado, valida la coherencia entre ellos con checks
deterministicos, consolida los escalamientos por severidad, y antes de fijar
el board pack final pide UNA sola aprobacion humana (HITL). Persiste todo a
cfo_state.json.

  1) Controller -> revisa el cierre, margenes, cartera
  2) Treasury   -> caja, burn, runway
  3) FP&A       -> forecast, variance MoM, variance vs presupuesto, anomalias
  4) cross-checks: los agentes deben concordar en los numeros compartidos
  5) consolidar escalamientos + UN gate humano
  6) board pack consolidado + acciones (Claude), fijados solo si el humano aprueba

Cada agente deja su analisis y sus flags en el estado compartido; el CFO los
consume. Los numeros los calculan los agentes por codigo (finance_core); el
modelo solo redacta. Una sola fuente de verdad, todo auditable.

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
import controller_agent
import treasury_agent
import fpa_agent

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIOD = "2026-05"
AGENTS = ["Controller", "Treasury", "FP&A"]


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

    # 1) op income del Controller == actual de operating income en la varianza de FP&A
    try:
        oi_ctrl = ctrl["pnl"]["operating_income"]
        oi_fpa = next(v for v in fpa["budget_variance"]["rows"]
                      if v["label"] == "Operating income")["actual"]
        if abs(oi_ctrl - oi_fpa) > 1:
            issues.append(f"op income no concuerda: Controller {oi_ctrl:,.0f} vs FP&A {oi_fpa:,.0f}")
    except (KeyError, TypeError, StopIteration):
        issues.append("faltan datos para conciliar op income entre Controller y FP&A")

    # 2) burn de Treasury == -op income (cuando hay perdida operativa)
    try:
        oi_ctrl = ctrl["pnl"]["operating_income"]
        expected_burn = -oi_ctrl if oi_ctrl < 0 else 0.0
        if abs(trez["burn"] - expected_burn) > 1:
            issues.append(f"burn de Treasury {trez['burn']:,.0f} != -op income {expected_burn:,.0f}")
    except (KeyError, TypeError):
        issues.append("faltan datos para conciliar burn de Treasury")

    return issues


def gather_escalations(ctx):
    """Junta los escalamientos de todos los agentes y los ordena por severidad."""
    esc = []
    for a in AGENTS:
        esc.extend(tuple(e) for e in ctx.get(a, "escalations", []))
    order = {"CRITICA": 0, "ALTA": 1}
    return sorted(esc, key=lambda e: order.get(e[0], 9))


def hitl_gate(esc):
    serios = [e for e in esc if e[0] in ("ALTA", "CRITICA")]
    if not serios:
        return True
    print("\n  [human-in-the-loop] Escalamientos que requieren tu visto bueno:")
    for sev, msg in serios:
        print(f"     - [{sev}] {msg}")
    try:
        return input("  Aprobas y fijas el board pack del CFO? [s/N]: ").strip().lower() == "s"
    except EOFError:
        return False


def compose_board_pack(ctx):
    ctrl = ctx.get("Controller", "narrative", "")
    trez = ctx.get("Treasury", "narrative", "")
    fpa = ctx.get("FP&A")
    fpa_bits = "\n".join(filter(None, [
        fpa.get("variance_expl", ""), fpa.get("budget_expl", ""), fpa.get("anomaly_expl", ""),
    ]))
    prompt = (
        f"--- Controller (cierre) ---\n{ctrl}\n\n"
        f"--- Treasury (liquidez) ---\n{trez}\n\n"
        f"--- FP&A (variance MoM, vs presupuesto, anomalias) ---\n{fpa_bits}\n\n"
        "Redacta el board pack consolidado del periodo."
    )
    return agent(
        "Sos el CFO. Con los insumos de Controller, Tesoreria y FP&A, escribi un board "
        "pack ejecutivo de 5-7 frases, tono CFO, directo, sin relleno. No agregues numeros nuevos.",
        prompt,
    )


def compose_actions(ctx):
    fpa = ctx.get("FP&A")
    esc = gather_escalations(ctx)
    esc_txt = "\n".join(f"  - [{s}] {m}" for s, m in esc) or "  (sin escalamientos)"
    prompt = (
        f"Escalamientos del periodo:\n{esc_txt}\n\n"
        f"Hallazgos de FP&A:\n{fpa.get('budget_expl', '')}\n{fpa.get('anomaly_expl', '')}\n\n"
        "Propone 3 acciones priorizadas, una linea cada una."
    )
    return agent(
        "Sos el CFO. Propones 3 acciones concretas, accionables y priorizadas a partir de "
        "los escalamientos y hallazgos. Una linea cada una. No agregues numeros nuevos; usa "
        "solo las cifras de los escalamientos y hallazgos dados.",
        prompt,
    )


# --- Pipeline del office -----------------------------------------------

def run(period=PERIOD):
    print("=" * 60)
    print(f"CFO OFFICE | cierre {period}")
    print("=" * 60)
    ctx = CFOContext()
    ctx.audit("CFO", "inicio", f"corriendo el office para {period}")

    print("\n[1/3] Controller...")
    controller_agent.run(ctx)
    print("\n[2/3] Treasury...")
    treasury_agent.run(ctx)
    print("\n[3/3] FP&A...")
    fpa_agent.run(ctx)

    # Checks de coherencia entre agentes (antes de escalar o redactar).
    issues = cross_checks(ctx)
    if issues:
        for i in issues:
            ctx.audit("cross_check", "FALLA", i)
        print("\n  Pipeline detenido: los agentes no concuerdan en los numeros.")
        ctx.put("CFO", {"status": "halted_inconsistent"})
        ctx.save()
        return ctx
    ctx.audit("cross_check", "ok", "agentes coherentes en los numeros compartidos")

    # Consolidar escalamientos de todos los agentes.
    esc = gather_escalations(ctx)
    for sev, msg in esc:
        ctx.audit("escalamiento", sev, msg)

    # Un solo gate humano antes de fijar el board pack del CFO.
    if not hitl_gate(esc):
        ctx.put("CFO", {"status": "rejected"})
        ctx.audit("CFO", "RECHAZADO", "el humano no aprobo; board pack no fijado")
        ctx.save()
        print("\n  Detenido por decision humana.")
        return ctx
    ctx.audit("CFO", "aprobado", "el humano aprobo continuar")

    board = compose_board_pack(ctx)
    actions = compose_actions(ctx)
    ctx.put("CFO", {"board_pack": board, "actions": actions, "status": "approved"})
    ctx.audit("CFO", "ok", "board pack consolidado y acciones fijados")

    print("\n--- BOARD PACK (CFO) ---\n" + board)
    print("\n--- ACCIONES PROPUESTAS ---\n" + actions)

    path = ctx.save()
    print(f"\nEstado compartido guardado en: {os.path.basename(path)} "
          f"({len(ctx.state['audit'])} eventos de audit)")
    return ctx


if __name__ == "__main__":
    run()
