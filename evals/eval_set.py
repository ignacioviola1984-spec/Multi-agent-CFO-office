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
    "cash_usd": 7092891,
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
