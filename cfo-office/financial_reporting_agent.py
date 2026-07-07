"""
financial_reporting_agent.py - Financial Reporting Agent (under Accounting & Reporting).

Produces the three primary financial statements from the closed books: income
statement, balance sheet, and (indirect) statement of cash flows. The statements
articulate by construction (net income rolls into equity; the cash flow ties to
the change in cash). Raises a flag only on a reporting-integrity issue (balance
does not foot, or cash flow does not tie). Numbers by code (finance_core); the
model only narrates.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz.
Correr:  python financial_reporting_agent.py
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


def agent(system, prompt, max_tokens=450):
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _money(x):
    return f"USD {x:,.0f}"


def reporting_escalations(inc, bs, cf):
    """Flags de reporting: solo integridad de los estados (no performance del
    negocio, que es de otros agentes). Si el balance no cuadra o el flujo de
    efectivo no ata, los estados no son confiables."""
    out = []
    if abs(bs["balance_check"]) > 1.0:
        out.append(["CRITICAL", f"balance sheet does not foot: imbalance {_money(bs['balance_check'])}"])
    if not cf["foots"]:
        out.append(["CRITICAL", "statement of cash flows does not tie to the change in cash"])
    return out


def run(ctx=None, period=PERIOD):
    own = ctx is None
    ctx = ctx or CFOContext()
    ctx.audit("Financial Reporting", "start", f"three statements {period}")

    inc = fc.income_statement(period)
    bs = fc.balance_sheet_statement(period)
    cf = fc.cash_flow_statement(period)
    esc = reporting_escalations(inc, bs, cf)

    facts = (
        f"Income statement {period}: revenue {_money(inc['revenue'])}, gross {_money(inc['gross'])} "
        f"({inc['gross_margin_pct']:.1f}%), operating/net income {_money(inc['net_income'])} "
        f"({inc['net_margin_pct']:.1f}%).\n"
        f"Balance sheet: total assets {_money(bs['total_assets'])} = liabilities {_money(bs['total_liabilities'])} "
        f"+ equity {_money(bs['total_equity'])} (foots, check {_money(bs['balance_check'])}); "
        f"cash {_money(bs['assets']['cash'])}.\n"
        f"Cash flow (indirect): operating {_money(cf['cfo'])}, investing {_money(cf['cfi'])}, "
        f"financing {_money(cf['cff'])}; net change {_money(cf['net_change'])} ties to change in cash "
        f"{_money(cf['actual_change'])} ({_money(cf['cash_begin'])} -> {_money(cf['cash_end'])})."
    )
    narrative = agent(
        "You are Financial Reporting. In 3-4 sentences, present the period's financials: the income "
        "statement headline, that the balance sheet foots, and that the statement of cash flows ties "
        "to the change in cash (the three statements articulate). Use only the figures given; do not "
        "invent numbers. Write in English.",
        facts,
    )

    ctx.put("Financial Reporting", {
        "income_statement": inc, "balance_sheet": bs, "cash_flow": cf,
        "narrative": narrative, "escalations": esc,
    })
    ctx.audit("Financial Reporting", "ok",
              f"statements produced; balance foots={abs(bs['balance_check'])<=1.0}, "
              f"cash flow ties={cf['foots']}; {len(esc)} escalation(s)")

    if own:
        print("\n--- FINANCIAL REPORTING ---\n" + narrative)
        path = ctx.save()
        print(f"\nShared state saved to: {os.path.basename(path)}")
    return ctx


if __name__ == "__main__":
    run()
