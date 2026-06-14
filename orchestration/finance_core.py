"""
finance_core.py - Calculo financiero deterministico (numeros crudos).

Lee los mismos CSV que el MCP server y devuelve numeros (no texto), para
que el orquestador pueda hacer cuentas (ej: runway = caja / burn) sin
depender del modelo. Una sola fuente de datos: ../finance-mcp/data.

Nota de arquitectura: en un refactor "de produccion", el MCP server
importaria estas funciones en vez de tener su propia copia de la logica.
Aca lo mantengo separado para no tocar el server ya validado.
"""

import csv
import os
import datetime

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "finance-mcp", "data")
LATEST = "2026-05"


def _load(name):
    with open(os.path.join(DATA, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_ENT = _load("entities.csv")
_FX = {(r["period"], r["currency"]): float(r["units_per_usd"]) for r in _load("fx_rates.csv")}
_PNL = _load("pnl_activity.csv")
_BS = _load("balance_sheet.csv")
_BUD = _load("budget.csv")
_INV = _load("ar_invoices.csv")
_CCY = {r["entity_id"]: r["currency"] for r in _ENT}


def _usd(amount_local, currency, period):
    return amount_local / _FX[(period, currency)]


def pnl_usd(period):
    """P&L consolidado en USD para un periodo. Devuelve numeros."""
    agg = {}
    for r in _PNL:
        if r["period"] != period:
            continue
        usd = _usd(float(r["amount_local"]), _CCY[r["entity_id"]], period)
        agg[r["account_code"]] = agg.get(r["account_code"], 0.0) + usd
    rev = agg.get("4000", 0.0)
    cogs = agg.get("5000", 0.0)
    gross = rev - cogs
    opex = agg.get("6000", 0.0) + agg.get("6100", 0.0) + agg.get("6200", 0.0)
    return {
        "revenue": rev,
        "cogs": cogs,
        "gross": gross,
        "opex": opex,
        "operating_income": gross - opex,
    }


def cash_total_usd():
    """Caja consolidada en USD al ultimo cierre."""
    return sum(
        _usd(float(r["amount_local"]), _CCY[r["entity_id"]], LATEST)
        for r in _BS if r["account_code"] == "1000"
    )


def ar_overdue_usd(as_of="2026-05-31"):
    """Cartera por cobrar en USD: corriente vs vencida (todo lo pasado de fecha).

    Aplica la definicion correcta: 'vencido' = cualquier factura abierta
    pasada su fecha de vencimiento (incluye el tramo 1-30).
    """
    asof = datetime.date.fromisoformat(as_of)
    current = 0.0
    overdue = 0.0
    for r in _INV:
        if r["status"] != "open":
            continue
        due = datetime.date.fromisoformat(r["due_date"])
        amt = _usd(float(r["amount_local"]), r["currency"], LATEST)
        if (asof - due).days <= 0:
            current += amt
        else:
            overdue += amt
    total = current + overdue
    return {"current": current, "overdue": overdue, "total": total,
            "overdue_pct": (overdue / total * 100) if total else 0}


# --------------------------------------------------------------------------
# Presupuesto y varianza (FP&A). Numeros deterministicos, calculados aca.
# --------------------------------------------------------------------------

def _budget_lines_usd(period):
    """Lineas del presupuesto consolidadas en USD (detalle por cuenta).

    El budget ya esta en USD (plan del grupo), no se convierte por FX.
    """
    agg = {}
    for r in _BUD:
        if r["period"] != period:
            continue
        agg[r["account_code"]] = agg.get(r["account_code"], 0.0) + float(r["amount_usd"])
    return agg


def _pnl_lines_usd(period):
    """Lineas de P&L (actuals) consolidadas en USD, detalle por cuenta."""
    agg = {}
    for r in _PNL:
        if r["period"] != period:
            continue
        usd = _usd(float(r["amount_local"]), _CCY[r["entity_id"]], period)
        agg[r["account_code"]] = agg.get(r["account_code"], 0.0) + usd
    return agg


def budget_usd(period):
    """Presupuesto consolidado en USD para un periodo (mismo shape que pnl_usd)."""
    b = _budget_lines_usd(period)
    rev = b.get("4000", 0.0)
    cogs = b.get("5000", 0.0)
    gross = rev - cogs
    opex = b.get("6000", 0.0) + b.get("6100", 0.0) + b.get("6200", 0.0)
    return {
        "revenue": rev,
        "cogs": cogs,
        "gross": gross,
        "opex": opex,
        "operating_income": gross - opex,
    }


def variance_usd(period):
    """Varianza presupuestaria consolidada en USD (actual vs budget) por linea.

    Para cada linea devuelve budget, actual, var ($) y var (%), y clasifica la
    varianza como 'F' (favorable) o 'U' (desfavorable) segun el tipo de linea:
    en ingresos/utilidad, mayor que el plan es favorable; en costos, mayor que
    el plan es desfavorable.

    El % usa el valor ABSOLUTO del budget en el denominador, para que el signo
    del % siga al signo del $ aun cuando el budget de un subtotal sea negativo
    (ej: una perdida operativa planificada). Asi no confunde la lectura.
    """
    a = _pnl_lines_usd(period)
    b = _budget_lines_usd(period)

    def line(label, actual, budget, kind):
        var = actual - budget
        favorable = (var >= 0) if kind == "income" else (var <= 0)
        pct = (var / abs(budget) * 100) if budget else 0.0
        return {
            "label": label, "actual": actual, "budget": budget,
            "var": var, "var_pct": pct, "kind": kind,
            "flag": "F" if favorable else "U",
        }

    rev_a, rev_b = a.get("4000", 0.0), b.get("4000", 0.0)
    cogs_a, cogs_b = a.get("5000", 0.0), b.get("5000", 0.0)
    sm_a, sm_b = a.get("6000", 0.0), b.get("6000", 0.0)
    rd_a, rd_b = a.get("6100", 0.0), b.get("6100", 0.0)
    ga_a, ga_b = a.get("6200", 0.0), b.get("6200", 0.0)
    gross_a, gross_b = rev_a - cogs_a, rev_b - cogs_b
    opex_a, opex_b = sm_a + rd_a + ga_a, sm_b + rd_b + ga_b
    oi_a, oi_b = gross_a - opex_a, gross_b - opex_b

    return [
        line("Revenue",           rev_a,   rev_b,   "income"),
        line("Cost of revenue",   cogs_a,  cogs_b,  "cost"),
        line("Gross profit",      gross_a, gross_b, "income"),
        line("Sales & marketing", sm_a,    sm_b,    "cost"),
        line("R&D",               rd_a,    rd_b,    "cost"),
        line("G&A",               ga_a,    ga_b,    "cost"),
        line("Total opex",        opex_a,  opex_b,  "cost"),
        line("Operating income",  oi_a,    oi_b,    "income"),
    ]


def material_variances(period, pct_threshold=5.0, usd_threshold=20000.0):
    """Lineas cuya varianza es material: |%| >= umbral Y |$| >= umbral.

    El doble umbral evita marcar lineas chicas con % alto o lineas grandes con
    % chico. Default 5% para una revision MENSUAL (10% dejaria pasar sobregastos
    reales de opex); el piso de USD 20k (~1.5% del revenue mensual consolidado)
    suprime swings absolutos triviales. Se pueden ajustar por argumento.
    """
    out = []
    for v in variance_usd(period):
        if abs(v["var_pct"]) >= pct_threshold and abs(v["var"]) >= usd_threshold:
            out.append(v)
    return out
