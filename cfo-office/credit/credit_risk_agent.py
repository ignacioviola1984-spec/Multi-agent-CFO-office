"""
credit_risk_agent.py - Credit Risk / Losses narrator (credit / LendingClub track).

Lee numeros deterministas de credit_core: cc.credit_risk(). Solo narra charge-off rate,
expected loss (USD y %), PD/LGD por grade y delinquency; nunca inventa ni recalcula una
cifra. Escala perdidas materiales (charge-off, expected loss, delinquency) por severidad.
"""
import os
import sys
from dotenv import load_dotenv
from anthropic import Anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))   # credit_core
sys.path.insert(0, os.path.join(ROOT, "cfo-office"))      # shared_state
import credit_core as cc
from shared_state import CFOContext

load_dotenv(os.path.join(ROOT, ".env"))
client = Anthropic()
MODEL = "claude-sonnet-4-6"


def agent(system, prompt, max_tokens=400):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def risk_escalations(charge_off_rate, expected_loss_pct, delinquency_rate):
    """Flags de riesgo por severidad, cada metrica EVALUADA DE FORMA INDEPENDIENTE:
    HIGH si supera su umbral critico, MEDIUM si queda en zona de atencion (>0.08).
    No se gatea una metrica detras de otra: una metrica en su propia banda escala
    aunque otra distinta sea critica. Solo perdidas crediticias materiales."""
    esc = []
    if charge_off_rate > 0.15:
        esc.append(["HIGH", f"charge-off rate {charge_off_rate * 100:.2f}%"])
    elif charge_off_rate > 0.08:
        esc.append(["MEDIUM", f"charge-off rate {charge_off_rate * 100:.2f}%"])
    if expected_loss_pct > 0.12:
        esc.append(["HIGH", f"expected loss {expected_loss_pct * 100:.2f}% of on-book balance"])
    elif expected_loss_pct > 0.08:
        esc.append(["MEDIUM", f"expected loss {expected_loss_pct * 100:.2f}% of on-book balance"])
    if delinquency_rate > 0.12:
        esc.append(["HIGH", f"delinquency {delinquency_rate * 100:.2f}%"])
    elif delinquency_rate > 0.08:
        esc.append(["MEDIUM", f"delinquency {delinquency_rate * 100:.2f}%"])
    return esc


def run(ctx=None):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Credit Risk", "start", "reading credit_risk()")

    # 1) deterministic numbers from credit_core (NEVER compute numbers in the model)
    cr = cc.credit_risk()

    charge_off_rate = cr["charge_off_rate"]
    charged_off_usd = cr["charged_off_usd"]
    pd_by_grade = cr["pd_by_grade"]
    lgd_by_grade = cr["lgd_by_grade"]
    expected_loss_usd = cr["expected_loss_usd"]
    expected_loss_pct = cr["expected_loss_pct"]
    onbook_outstanding_usd = cr["onbook_outstanding_usd"]
    n_delinquent = cr["n_delinquent"]
    delinquency_rate = cr["delinquency_rate"]

    # 2) escalations as [SEVERITY, message] lists (HIGH / MEDIUM)
    esc = risk_escalations(charge_off_rate, expected_loss_pct, delinquency_rate)

    # 3) build a facts string from data, then narrate
    pd_lines = []
    for grade in sorted(pd_by_grade):
        pd = pd_by_grade[grade]
        lgd = lgd_by_grade.get(grade)
        lgd_str = f"{lgd * 100:.2f}%" if lgd is not None else "n/a"
        pd_lines.append(f"  {grade}: PD {pd * 100:.2f}%, LGD {lgd_str}")
    pd_block = "\n".join(pd_lines) if pd_lines else "  (no grade-level data)"

    esc_txt = "\n".join(f"  - [{s}] {m}" for s, m in esc) or "  (none breach the escalation thresholds)"
    facts = (
        "Credit risk / losses (LendingClub credit track).\n"
        f"Charge-off rate (matured loans): {charge_off_rate * 100:.2f}%\n"
        f"Charged-off principal: ${charged_off_usd:,.0f}\n"
        f"On-book outstanding balance: ${onbook_outstanding_usd:,.0f}\n"
        f"Expected loss (modeled PROXY: realized PD by grade x LGD x outstanding, not a booked "
        f"figure): ${expected_loss_usd:,.0f} ({expected_loss_pct * 100:.2f}% of on-book balance)\n"
        f"Delinquent loans: {n_delinquent} (delinquency rate {delinquency_rate * 100:.2f}%)\n"
        "PD / LGD by grade (best to worst):\n"
        f"{pd_block}\n"
        "Engine escalations (the deterministic verdict on what is elevated):\n"
        f"{esc_txt}"
    )

    narrative = agent(
        "You are the Credit Risk / Losses analyst for a LendingClub-based credit portfolio. "
        "Write 3-4 sentences in English summarizing credit risk and losses. "
        "Use ONLY the numbers provided; never invent or recompute any figure, and do NOT make an "
        "independent 'elevated/healthy' judgment — treat the engine escalations list as the "
        "authoritative verdict on what is elevated. Cover the realized charge-off rate, the expected "
        "loss (state it is a documented modeled PROXY, not a booked figure), the PD-by-grade trend, "
        "and the delinquency rate.",
        facts,
    )

    # 4) leave the structured result + narrative in the shared book
    ctx.put("Credit Risk", {
        "charge_off_rate": charge_off_rate,
        "charged_off_usd": charged_off_usd,
        "pd_by_grade": pd_by_grade,
        "lgd_by_grade": lgd_by_grade,
        "expected_loss_usd": expected_loss_usd,
        "expected_loss_pct": expected_loss_pct,
        "onbook_outstanding_usd": onbook_outstanding_usd,
        "n_delinquent": n_delinquent,
        "delinquency_rate": delinquency_rate,
        "narrative": narrative,
        "escalations": esc,
    })
    ctx.audit(
        "Credit Risk", "ok",
        f"charge-off {charge_off_rate * 100:.2f}%, expected loss ${expected_loss_usd:,.0f} "
        f"({expected_loss_pct * 100:.2f}%); {len(esc)} escalation(s)",
    )

    if own:
        print("\n--- CREDIT RISK ---\n" + narrative)
        path = ctx.save()
        print("\nShared state saved to: " + os.path.basename(path))
    return ctx


if __name__ == "__main__":
    run()
