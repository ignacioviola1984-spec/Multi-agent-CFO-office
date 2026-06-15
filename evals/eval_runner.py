"""
eval_runner.py - Eval harness y medicion (Fase 5.2).

Corre tres suites contra los agentes del repo y devuelve un scorecard con
accuracy. Sale con codigo distinto de cero si algo no pasa, asi sirve como
test de regresion (lo podes correr antes de cada cambio para saber si
rompiste algo).

  1) Numbers     - regresion sobre numeros consolidados (deterministicos).
  2) Extraction  - accuracy de la extraccion de terminos de contratos.
  3) Grounding   - guardrail: el RAG debe negarse ante preguntas sin
                   respuesta en los documentos, no inventar.

Requisitos: ANTHROPIC_API_KEY en el .env de la raiz (para extraction y
grounding). La suite de numbers no usa el modelo.

Correr:  python eval_runner.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "orchestration"))
sys.path.insert(0, os.path.join(ROOT, "document-intelligence"))

import finance_core as fc
from eval_set import (
    EXTRACTION_TRUTH, NUMERIC_TRUTH, NUMERIC_TOLERANCE, AR_OVERDUE_PCT_MIN,
    VARIANCE_TRUTH, VARIANCE_MATERIAL_COUNT, STRATEGIC_TRUTH, ADMIN_TRUTH,
    FORECAST_TRUTH, CONTROLS_TRUTH, RTR_TRUTH, GROUNDING_CASES, REFUSAL_SIGNALS,
)


def _check(label, ok, detail=""):
    mark = "PASS" if ok else "FALLA"
    print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")
    return 1 if ok else 0


def suite_numbers():
    print("\nSuite 1 - Numbers (regresion, sin modelo)")
    passed = total = 0
    pnl = fc.pnl_usd("2026-05")
    cash = fc.cash_total_usd()
    ar = fc.ar_overdue_usd()

    checks = [
        ("operating income 2026-05", pnl["operating_income"], NUMERIC_TRUTH["operating_income_2026_05_usd"]),
        ("cash consolidado", cash, NUMERIC_TRUTH["cash_usd"]),
    ]
    for label, got, expected in checks:
        ok = abs(got - expected) <= abs(expected) * NUMERIC_TOLERANCE
        total += 1
        passed += _check(label, ok, f"esperado ~{expected:,.0f}, obtenido {got:,.0f}")

    ok = ar["overdue_pct"] >= AR_OVERDUE_PCT_MIN
    total += 1
    passed += _check("AR vencida > 90%", ok, f"obtenido {ar['overdue_pct']:.0f}%")

    # Regresion sobre la varianza presupuestaria (deterministica).
    var = {v["label"]: v for v in fc.variance_usd("2026-05")}
    var_checks = [
        ("varianza operating income", var["Operating income"]["var"], VARIANCE_TRUTH["operating_income_var_usd"]),
        ("varianza G&A", var["G&A"]["var"], VARIANCE_TRUTH["ga_var_usd"]),
    ]
    for label, got, expected in var_checks:
        tol = max(abs(expected) * NUMERIC_TOLERANCE, 1.0)
        ok = abs(got - expected) <= tol
        total += 1
        passed += _check(label, ok, f"esperado ~{expected:,.0f}, obtenido {got:,.0f}")

    n_mat = len(fc.material_variances("2026-05"))
    ok = n_mat == VARIANCE_MATERIAL_COUNT
    total += 1
    passed += _check("lineas materiales vs presupuesto", ok,
                     f"esperado {VARIANCE_MATERIAL_COUNT}, obtenido {n_mat}")

    # Regresion sobre las metricas estrategicas (deterministicas).
    sm = fc.strategic_metrics()
    strat_checks = [
        ("run-rate (ARR proxy)", sm["run_rate"], STRATEGIC_TRUTH["run_rate_usd"]),
        ("burn multiple", sm["burn_multiple"], STRATEGIC_TRUTH["burn_multiple"]),
        ("Rule of 40", sm["rule_of_40"], STRATEGIC_TRUTH["rule_of_40"]),
    ]
    for label, got, expected in strat_checks:
        tol = max(abs(expected) * NUMERIC_TOLERANCE, 0.05)
        ok = abs(got - expected) <= tol
        total += 1
        passed += _check(label, ok, f"esperado ~{expected:,.2f}, obtenido {got:,.2f}")

    # Regresion sobre Administration (AR/AP/Tax), deterministica.
    ap = fc.ap_metrics()
    tx = fc.tax_metrics()
    admin_checks = [
        ("overdue payables (AP)", ap["overdue"], ADMIN_TRUTH["ap_overdue_usd"]),
        ("overdue tax", tx["overdue"], ADMIN_TRUTH["tax_overdue_usd"]),
    ]
    for label, got, expected in admin_checks:
        tol = max(abs(expected) * NUMERIC_TOLERANCE, 1.0)
        ok = abs(got - expected) <= tol
        total += 1
        passed += _check(label, ok, f"esperado ~{expected:,.0f}, obtenido {got:,.0f}")

    # Regresion sobre el forecast de caja a 13 semanas.
    f13 = fc.cash_forecast_13w()
    exp = FORECAST_TRUTH["ending_cash_13w_usd"]
    ok = abs(f13["ending_cash"] - exp) <= abs(exp) * NUMERIC_TOLERANCE
    total += 1
    passed += _check("13-week ending cash", ok, f"esperado ~{exp:,.0f}, obtenido {f13['ending_cash']:,.0f}")
    ok = f13["week_cash_negative"] == FORECAST_TRUTH["week_cash_negative"]
    total += 1
    passed += _check("13-week cash stays positive", ok, f"week_negative={f13['week_cash_negative']}")

    # Regresion sobre el registro de controles internos (deterministico).
    ctrl = fc.control_checks()
    ok = ctrl["books_balanced"] == CONTROLS_TRUTH["books_balanced"]
    total += 1
    passed += _check("books balance (trial balance)", ok, f"max imbalance USD {ctrl['max_bs_imbalance']:.2f}")
    ok = ctrl["n_fail"] == CONTROLS_TRUTH["n_control_failures"]
    total += 1
    passed += _check("no control failures (integrity)", ok,
                     f"esperado {CONTROLS_TRUTH['n_control_failures']}, obtenido {ctrl['n_fail']}")
    ok = ctrl["approval_exceptions"] == CONTROLS_TRUTH["approval_exceptions"]
    total += 1
    passed += _check("disbursement authorization exceptions", ok,
                     f"esperado {CONTROLS_TRUTH['approval_exceptions']}, obtenido {ctrl['approval_exceptions']}")
    exp = CONTROLS_TRUTH["approval_exceptions_total_usd"]
    ok = abs(ctrl["approval_exceptions_total"] - exp) <= abs(exp) * NUMERIC_TOLERANCE
    total += 1
    passed += _check("authorization exceptions total", ok,
                     f"esperado ~{exp:,.0f}, obtenido {ctrl['approval_exceptions_total']:,.0f}")

    # Regresion sobre Record-to-Report (cierre, estados financieros, auditoria).
    cr = fc.close_reconciliations()
    ok = (cr["all_reconciled"] == RTR_TRUTH["close_reconciled"]
          and cr["n_open_items"] == RTR_TRUTH["close_open_items"])
    total += 1
    passed += _check("close reconciles (subledgers tie to GL)", ok,
                     f"open items {cr['n_open_items']}, articulation {cr['articulation']['status']}")

    inc = fc.income_statement()
    bs = fc.balance_sheet_statement()
    cf = fc.cash_flow_statement()
    exp = RTR_TRUTH["net_income_usd"]
    ok = abs(inc["net_income"] - exp) <= abs(exp) * NUMERIC_TOLERANCE
    total += 1
    passed += _check("reporting net income", ok, f"esperado ~{exp:,.0f}, obtenido {inc['net_income']:,.0f}")
    ok = (abs(bs["balance_check"]) <= 1.0) == RTR_TRUTH["balance_foots"]
    total += 1
    passed += _check("balance sheet foots (A = L + E)", ok, f"check USD {bs['balance_check']:.2f}")
    ok = cf["foots"] == RTR_TRUTH["cash_flow_foots"]
    total += 1
    passed += _check("cash flow ties to change in cash", ok,
                     f"statement {cf['net_change']:,.0f} vs actual {cf['actual_change']:,.0f}")

    au = fc.audit_procedures()
    ok = (au["opinion"] == RTR_TRUTH["audit_opinion"]
          and au["n_exceptions"] == RTR_TRUTH["audit_exceptions"])
    total += 1
    passed += _check("audit opinion unqualified", ok,
                     f"opinion {au['opinion']}, {au['n_exceptions']} exception(s)")
    return passed, total


def suite_extraction():
    print("\nSuite 2 - Extraction (accuracy de extraccion de contratos)")
    from rag import extract_terms
    rows = {r["_file"]: r for r in extract_terms()}
    passed = total = 0
    for fname, expected in EXTRACTION_TRUTH.items():
        row = rows.get(fname, {})
        for field, must_contain in expected.items():
            got = str(row.get(field, "")).lower()
            ok = must_contain.lower() in got
            total += 1
            passed += _check(f"{fname} :: {field}", ok, f"esperaba '{must_contain}'")
    return passed, total


def suite_grounding():
    print("\nSuite 3 - Grounding guardrail (debe negarse, no inventar)")
    from rag import answer
    passed = total = 0
    for q in GROUNDING_CASES:
        _, txt = answer(q)
        low = txt.lower()
        refused = any(sig in low for sig in REFUSAL_SIGNALS)
        total += 1
        passed += _check(f"se niega a: {q}", refused)
    return passed, total


def main():
    print("=" * 60)
    print("EVAL HARNESS - confiabilidad de los agentes")
    print("=" * 60)

    results = []
    results.append(("Numbers", suite_numbers()))
    results.append(("Extraction", suite_extraction()))
    results.append(("Grounding", suite_grounding()))

    print("\n" + "=" * 60)
    print("SCORECARD")
    print("=" * 60)
    all_ok = True
    tot_p = tot_t = 0
    for name, (p, t) in results:
        tot_p += p
        tot_t += t
        pct = (p / t * 100) if t else 0
        status = "OK" if p == t else "REVISAR"
        if p != t:
            all_ok = False
        print(f"  {name:12} {p}/{t}  ({pct:.0f}%)  {status}")
    overall = (tot_p / tot_t * 100) if tot_t else 0
    print("-" * 60)
    print(f"  {'TOTAL':12} {tot_p}/{tot_t}  ({overall:.0f}%)")

    if not all_ok:
        print("\nHay checks que no pasaron. Salida con codigo 1 (regresion).")
        sys.exit(1)
    print("\nTodos los checks pasaron.")


if __name__ == "__main__":
    main()
