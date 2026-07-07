"""
internal_controls_agent.py - Internal Controls Agent del CFO office.

La capa de aseguramiento (estilo SOX): corre un registro de controles sobre la
INTEGRIDAD de los datos y del proceso, no sobre la performance del negocio. Es
deliberadamente distinto de los demas agentes para no pisar dueños:

  - Controller / Treasury / Administration / FP&A / Strategic miden el negocio
    (margenes, caja, capital de trabajo, varianza, eficiencia).
  - Internal Controls testea que los libros cuadren, que no falten tasas, que no
    haya posteos con fecha futura, que no haya pagos duplicados, y que los
    desembolsos grandes lleven autorizacion. Escala FALLAS DE CONTROL, nunca
    riesgos ya escalados por su dueno (runway, AR/AP/tax vencidos, perdida op).

Numeros y veredictos por codigo (deterministicos, finance_core.control_checks);
el modelo solo redacta el resumen. Asi el harness de evals puede testear los
controles sin depender del modelo.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python internal_controls_agent.py
"""

import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # finance_core
sys.path.insert(0, HERE)                                  # shared_state

import finance_core as fc
from shared_state import CFOContext

# Secrets are surfaced through the SecretsProvider (config/secrets.py): load the
# repo-root .env via the provider, so the Anthropic API key is retrieved through
# the secrets interface rather than a scattered os.environ / load_dotenv call.
sys.path.insert(0, os.path.abspath(ROOT))
from config import secrets as appsecrets  # noqa: E402
appsecrets.load_env(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"

PERIOD = "2026-05"


def agent(system, prompt, max_tokens=400):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def controls_escalations(res):
    """Flags de control por severidad: un escalamiento por cada control que no
    pasa (FAIL = integridad rota; EXCEPTION = items que requieren revision).
    No se duplican riesgos de otros agentes: aca solo van fallas de control."""
    out = []
    for c in res["checks"]:
        if c["status"] in ("FAIL", "EXCEPTION"):
            out.append([c["severity"], f"{c['name']}: {c['detail']}"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Internal Controls", "start", f"control testing {period}")

    res = fc.control_checks(period)
    esc = controls_escalations(res)

    register = "\n".join(
        f"[{c['id']}] {c['name']}: {c['status']} - {c['detail']}" for c in res["checks"]
    )
    facts = (
        f"Internal control register for {period} "
        f"({res['n_pass']} passed, {res['n_fail']} failed, {res['n_exception']} exception(s)):\n"
        f"{register}"
    )
    narrative = agent(
        "You are Internal Controls (assurance). In 2-3 sentences, state the control posture: "
        "confirm what integrity controls passed (books balance, FX completeness, posting cutoff) "
        "and name the exception(s) that need action. Use only the register given; do not invent "
        "figures and do not restate business risks (runway, overdue balances). Write in English.",
        facts,
    )

    ctx.put("Internal Controls", {
        "checks": res["checks"], "summary": {
            "n_pass": res["n_pass"], "n_fail": res["n_fail"], "n_exception": res["n_exception"],
            "books_balanced": res["books_balanced"], "max_bs_imbalance": res["max_bs_imbalance"],
            "approval_exceptions": res["approval_exceptions"],
            "approval_exceptions_total": res["approval_exceptions_total"],
        },
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Internal Controls", "ok",
              f"{res['n_pass']} pass / {res['n_fail']} fail / {res['n_exception']} exc; "
              f"{len(esc)} escalation(s)")

    if own:
        print("\n--- INTERNAL CONTROLS ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
