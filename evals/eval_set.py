"""
eval_set.py - Ground truth para evaluar los agentes del repo (Fase 5.1).

Un eval set es un conjunto de casos con respuesta correcta conocida. Sin
esto no se puede afirmar que un agente es confiable: solo se puede esperar
que lo sea. Aca definimos la verdad contra la cual medimos.
"""

# 1) Extraccion de contratos: substrings que DEBEN aparecer en cada campo
#    extraido. Se valida por "contiene" (no exact match) porque el modelo
#    puede frasear distinto ("Net 45 days" vs "net 45 days from invoice").
EXTRACTION_TRUTH = {
    "contract_cloudhost.md": {"counterparty": "CloudHost", "payment_terms": "30", "governing_law": "Delaware"},
    "contract_legal.md":     {"counterparty": "Brightwater", "payment_terms": "15", "governing_law": "England"},
    "contract_marketing.md": {"counterparty": "Northpeak", "payment_terms": "45", "governing_law": "New York"},
}

# 2) Numeros financieros consolidados (cierre 2026-05). Son deterministicos;
#    aca actuan como test de REGRESION: si un cambio los mueve, el eval falla.
NUMERIC_TRUTH = {
    "operating_income_2026_05_usd": -756823,
    "cash_usd": 7504278,    # balance articulado: caja = activo de cuadre (AR/AP atan al subledger)
}
NUMERIC_TOLERANCE = 0.01            # 1%
AR_OVERDUE_PCT_MIN = 90            # la cartera vencida debe superar el 90%

# 2b) Varianza presupuestaria (cierre 2026-05). Tambien deterministica: actua
#     como test de regresion sobre el sub-agente de FP&A. Convencion: var =
#     actual - budget; en operating income un valor negativo es desfavorable.
VARIANCE_TRUTH = {
    "operating_income_var_usd": -38938,   # actual peor que el plan (desfavorable)
    "ga_var_usd": 20935,                  # sobregasto de G&A (desfavorable)
}
VARIANCE_MATERIAL_COUNT = 2               # G&A y operating income (umbral 5% / USD 20k)

# 2c) Metricas estrategicas (cierre 2026-05). Deterministicas -> regresion sobre
#     el Strategic Finance Agent (la matematica vive en finance_core).
STRATEGIC_TRUTH = {
    "run_rate_usd": 14856124,   # revenue mensual x 12 (ARR proxy)
    "burn_multiple": 11.62,     # burn mensual / revenue nuevo mensual
    "rule_of_40": 42.4,         # crecimiento anualizado (proxy) + margen operativo
}

# 2d) Administration (AR/AP/Tax). Deterministicas -> regresion sobre el
#     Administration Agent y sus sub-agentes.
ADMIN_TRUTH = {
    "ap_overdue_usd": 416764,   # cuentas por pagar vencidas
    "tax_overdue_usd": 118496,  # obligaciones impositivas vencidas
}

# 2e) Forecast de caja a 13 semanas (Treasury). Deterministico -> regresion.
FORECAST_TRUTH = {
    "ending_cash_13w_usd": 5551607,   # caja al final de la semana 13
    "week_cash_negative": None,        # no se vuelve negativa en el horizonte
}

# 2f) Internal Controls. Deterministico -> regresion sobre el control register.
#     Los controles de integridad deben pasar (libros cuadran, FX completo, sin
#     posteos futuros, sin duplicados) y el unico hallazgo es la excepcion de
#     autorizacion de desembolsos sobre el tope de USD 25k.
CONTROLS_TRUTH = {
    "books_balanced": True,
    "n_control_failures": 0,              # ninguna falla de integridad (C1-C3)
    "approval_exceptions": 6,             # pagos sobre el tope de autorizacion (C5)
    "approval_exceptions_total_usd": 217269,
}

# 2g) Record-to-Report: cierre, estados financieros y auditoria. Deterministicos
#     -> regresion sobre Accounting & Close, Financial Reporting y Audit. El
#     balance se genera articulado, asi que el cierre RECONCILIA, los 3 estados
#     ATAN y el flujo de efectivo CUADRA -> la auditoria opina sin salvedades.
RTR_TRUTH = {
    "close_reconciled": True,    # subledgers AR/AP atan al GL y RE articula
    "close_open_items": 0,
    "net_income_usd": -756823,   # resultado neto == resultado operativo (sin lineas debajo)
    "balance_foots": True,       # A = P + PN
    "cash_flow_foots": True,     # el flujo de efectivo ata a la variacion de caja
    "audit_opinion": "unqualified",
    "audit_exceptions": 0,
}

# 3) Guardrail de grounding: preguntas SIN respuesta en los documentos.
#    El agente debe NEGARSE a responder, no inventar. Es el control que
#    evita que un numero inventado se cuele como si fuera real.
GROUNDING_CASES = [
    "What is the CEO base salary?",
    "How many full-time employees does the company have?",
]
REFUSAL_SIGNALS = [
    "no ", "not ", "cannot", "can't", "does not", "doesn't", "insufficient",
    "no puede", "no contiene", "no hay", "sin informacion", "no se puede",
]
