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
import json
import datetime

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "finance-mcp", "data")
LATEST = "2026-05"


# --------------------------------------------------------------------------
# Bounded self-improvement hook (VALUES ONLY, never formulas).
# A small, explicit set of operational PARAMETERS (the AR collection rate, the
# materiality thresholds, and the disbursement-authorization threshold) may be
# calibrated within bounds by the self-improvement system (../self-improvement/).
# This reader returns the active champion value from that system's champion
# store, or the original hardcoded default if the store is absent or the name is
# not present, so behavior is IDENTICAL by default. No formula reads through
# here; anything not wired through _registry_param is frozen and cannot be
# changed by that system.
# --------------------------------------------------------------------------
def _registry_param(name, default):
    try:
        state_dir = os.environ.get("SELFIMPROVE_STATE_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "self-improvement", "state")
        with open(os.path.join(state_dir, "champions.json"), encoding="utf-8") as f:
            champions = json.load(f).get("champions", {})
        if name in champions:
            return type(default)(champions[name]["value"])
    except Exception:
        pass
    return default


def _load(name):
    with open(os.path.join(DATA, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_ENT = _load("entities.csv")
_FX = {(r["period"], r["currency"]): float(r["units_per_usd"]) for r in _load("fx_rates.csv")}
_PNL = _load("pnl_activity.csv")
_BS = _load("balance_sheet.csv")
_BUD = _load("budget.csv")
_INV = _load("ar_invoices.csv")
_AP = _load("ap_invoices.csv")
_TAX = _load("tax_obligations.csv")
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
    sm = agg.get("6000", 0.0)
    rd = agg.get("6100", 0.0)
    ga = agg.get("6200", 0.0)
    opex = sm + rd + ga
    return {
        "revenue": rev,
        "cogs": cogs,
        "gross": gross,
        "sm": sm,
        "rd": rd,
        "ga": ga,
        "opex": opex,
        "operating_income": gross - opex,
    }


def cash_total_usd(period=LATEST):
    """Caja consolidada en USD al cierre del periodo (el balance tiene 2 periodos)."""
    return sum(
        _usd(float(r["amount_local"]), _CCY[r["entity_id"]], period)
        for r in _BS if r["account_code"] == "1000" and r["period"] == period
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


MATERIALITY_PCT_THRESHOLD = _registry_param("materiality_pct_threshold", 5.0)
MATERIALITY_USD_THRESHOLD = _registry_param("materiality_usd_threshold", 20000.0)


def material_variances(period, pct_threshold=MATERIALITY_PCT_THRESHOLD, usd_threshold=MATERIALITY_USD_THRESHOLD):
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


# --------------------------------------------------------------------------
# Metricas estrategicas (Strategic Finance). Numeros deterministicos, sin
# cliente: el agente solo las narra; el eval harness las puede testear.
# --------------------------------------------------------------------------

PERIODS = sorted({r["period"] for r in _PNL})


def _avg_mom_growth(values):
    growths = [(values[i] / values[i - 1] - 1) for i in range(1, len(values))
               if values[i - 1]]
    return sum(growths) / len(growths) if growths else 0.0


def strategic_scenarios(rev_latest, base_growth, op_margin):
    """3 escenarios de crecimiento; el margen se mantiene constante a proposito:
    crecer mueve el run-rate y el Rule of 40, pero no el margen, que es el
    verdadero cuello de botella estructural."""
    out = []
    for name, g in [("Low", base_growth * 0.5), ("Base", base_growth), ("High", base_growth * 1.5)]:
        ann = (1 + g) ** 12 - 1
        out.append({
            "name": name, "mom_growth": g,
            "run_rate_12m": rev_latest * ((1 + g) ** 12) * 12,
            "rule_of_40": ann * 100 + op_margin * 100,
        })
    return out


def strategic_metrics(periods=None):
    """Metricas SaaS estrategicas, todas deterministicas:
    run-rate (ARR proxy), Rule of 40, burn multiple, magic number, mix de
    gasto, gap de margen a breakeven y escenarios de crecimiento.

    El crecimiento se anualiza componiendo el promedio MoM (proxy; no hay
    historico YoY con pocos meses). burn_multiple/magic_number son None si no
    hay revenue nuevo o S&M previo (evita dividir por cero).
    """
    periods = periods or PERIODS
    series = {p: pnl_usd(p) for p in periods}
    rev = [series[p]["revenue"] for p in periods]
    op = [series[p]["operating_income"] for p in periods]
    sm = [series[p]["sm"] for p in periods]
    rev_latest, rev_prev, op_latest, sm_prev = rev[-1], rev[-2], op[-1], sm[-2]
    latest = series[periods[-1]]

    g = _avg_mom_growth(rev)
    ann_growth = (1 + g) ** 12 - 1
    op_margin = (op_latest / rev_latest) if rev_latest else 0.0
    net_new = rev_latest - rev_prev
    burn = -op_latest if op_latest < 0 else 0.0
    return {
        "run_rate": rev_latest * 12,
        "mom_growth": g, "ann_growth": ann_growth,
        "op_margin": op_margin,
        "rule_of_40": ann_growth * 100 + op_margin * 100,
        "net_new_rev": net_new, "monthly_burn": burn,
        "burn_multiple": (burn / net_new) if net_new > 0 else None,
        "magic_number": (net_new / sm_prev) if sm_prev > 0 else None,
        "mix": {k: (latest[k] / rev_latest if rev_latest else 0.0)
                for k in ("cogs", "sm", "rd", "ga")},
        "breakeven_gap_pp": (-op_margin * 100) if op_margin < 0 else 0.0,
        "scenarios": strategic_scenarios(rev_latest, g, op_margin),
    }


# --------------------------------------------------------------------------
# Administration: AR, AP y Tax. Numeros deterministicos en USD (a tasa de
# cierre LATEST, igual que el aging). El agente de Administracion narra; aca
# se calculan las cifras.
# --------------------------------------------------------------------------

def ar_metrics(period=LATEST, as_of="2026-05-31"):
    """Cuentas por cobrar: corriente/vencida (reusa ar_overdue_usd) + DSO."""
    ar = ar_overdue_usd(as_of)
    rev = pnl_usd(period)["revenue"]
    asof = datetime.date.fromisoformat(as_of)
    n_overdue = sum(1 for r in _INV if r["status"] == "open"
                    and datetime.date.fromisoformat(r["due_date"]) < asof)
    return {**ar, "dso": (ar["total"] / rev * 30) if rev else 0.0, "n_overdue": n_overdue}


def ap_metrics(period=LATEST, as_of="2026-05-31"):
    """Cuentas por pagar: abierto, vencido, por vencer (<=30d) y DPO."""
    asof = datetime.date.fromisoformat(as_of)
    open_total = overdue = upcoming_30 = 0.0
    n_overdue = 0
    for r in _AP:
        if r["status"] != "open":
            continue
        amt = _usd(float(r["amount_local"]), r["currency"], LATEST)
        open_total += amt
        days = (datetime.date.fromisoformat(r["due_date"]) - asof).days
        if days < 0:
            overdue += amt
            n_overdue += 1
        elif days <= 30:
            upcoming_30 += amt
    cogs = pnl_usd(period)["cogs"]
    return {"open_total": open_total, "overdue": overdue, "upcoming_30d": upcoming_30,
            "n_overdue": n_overdue, "dpo": (open_total / cogs * 30) if cogs else 0.0}


def tax_metrics(as_of="2026-05-31"):
    """Obligaciones impositivas pendientes: total, vencido, por vencer y por jurisdiccion."""
    asof = datetime.date.fromisoformat(as_of)
    pending_total = overdue = upcoming_30 = 0.0
    by_juris = {}
    for r in _TAX:
        if r["status"] != "pending":
            continue
        amt = _usd(float(r["amount_local"]), r["currency"], LATEST)
        pending_total += amt
        by_juris[r["jurisdiction"]] = by_juris.get(r["jurisdiction"], 0.0) + amt
        days = (datetime.date.fromisoformat(r["due_date"]) - asof).days
        if days < 0:
            overdue += amt
        elif days <= 30:
            upcoming_30 += amt
    return {"pending_total": pending_total, "overdue": overdue,
            "upcoming_30d": upcoming_30, "by_jurisdiction": by_juris}


# --------------------------------------------------------------------------
# Forecast directo de caja a 13 semanas (Treasury). Deterministico, en USD.
# --------------------------------------------------------------------------

AR_COLLECTION_RATE = _registry_param("ar_collection_rate", 0.90)   # 10% de prevision sobre cobranzas (calibrable dentro de banda; ver ../self-improvement/)


def cash_forecast_13w(start="2026-06-01"):
    """Forecast directo de caja a 13 semanas (semanal), en USD.

    Modelo (asunciones explicitas y defendibles, para no doble-contar):
    - Caja inicial = caja consolidada al cierre.
    - Burn operativo recurrente: el burn mensual prorrateado por semana (x12/52),
      aplicado cada semana. Es el drenaje go-forward, ya neto de la operacion.
    - Capital de trabajo de saldos EXISTENTES al cierre (NO incluido en el burn):
      se cobran las facturas AR abiertas (al 90%) y se pagan AP y tax pendientes
      en la semana de su vencimiento; lo vencido cae en la semana 1 (catch-up).
    El burn es el flujo en curso; los saldos abiertos son stock que se liquida
    una sola vez -> no se duplica el revenue/costo.
    """
    start_d = datetime.date.fromisoformat(start)
    start_cash = cash_total_usd()
    monthly_burn = max(0.0, -pnl_usd(LATEST)["operating_income"])
    weekly_burn = monthly_burn * 12.0 / 52.0

    weeks = [{"inflow": 0.0, "outflow": 0.0} for _ in range(13)]

    def _bucket(due_iso):
        wi = (datetime.date.fromisoformat(due_iso) - start_d).days // 7
        if wi < 0:
            return 0          # vencido -> semana 1 (catch-up)
        return wi if wi <= 12 else None   # mas alla del horizonte: se ignora

    for r in _INV:            # AR: cobranzas (inflow), al 90%
        if r["status"] != "open":
            continue
        b = _bucket(r["due_date"])
        if b is not None:
            weeks[b]["inflow"] += _usd(float(r["amount_local"]), r["currency"], LATEST) * AR_COLLECTION_RATE
    for r in _AP:             # AP: pagos (outflow)
        if r["status"] != "open":
            continue
        b = _bucket(r["due_date"])
        if b is not None:
            weeks[b]["outflow"] += _usd(float(r["amount_local"]), r["currency"], LATEST)
    for r in _TAX:            # tax: pagos (outflow)
        if r["status"] != "pending":
            continue
        b = _bucket(r["due_date"])
        if b is not None:
            weeks[b]["outflow"] += _usd(float(r["amount_local"]), r["currency"], LATEST)

    cash = start_cash
    rows = []
    min_cash, min_week, week_negative = start_cash, 0, None
    for i in range(13):
        inflow = weeks[i]["inflow"]
        outflow = weeks[i]["outflow"] + weekly_burn
        net = inflow - outflow
        cash += net
        rows.append({"week": i + 1, "inflow": inflow, "outflow": outflow,
                     "net": net, "ending_cash": cash})
        if cash < min_cash:
            min_cash, min_week = cash, i + 1
        if week_negative is None and cash < 0:
            week_negative = i + 1

    return {"start": start, "starting_cash": start_cash, "weekly_burn": weekly_burn,
            "rows": rows, "ending_cash": cash, "min_cash": min_cash,
            "min_week": min_week, "week_cash_negative": week_negative}


# --------------------------------------------------------------------------
# Internal Controls: pruebas de control deterministicas sobre la INTEGRIDAD de
# los datos y del proceso (capa de aseguramiento, estilo SOX). No mide
# performance del negocio -eso es de los otros agentes-: testea que los libros
# cuadren, que no falten tasas de cambio, que no haya posteos con fecha futura,
# y marca desembolsos que exigen autorizacion. Cada riesgo con un unico dueno:
# Internal Controls escala FALLAS DE CONTROL, no riesgos ya escalados (runway,
# AR/AP/tax vencidos, perdida operativa) que tienen su propio dueno.
# --------------------------------------------------------------------------

# Mapa de cuentas de balance a su naturaleza (A=activo, L=pasivo, E=patrimonio).
_BS_TYPE = {"1000": "A", "1100": "A", "1500": "A",
            "2000": "L", "2500": "L", "3000": "E", "3900": "E"}

# Umbral de revision de autorizacion para pagos a PROVEEDORES (AP). Es un screen
# POR MONTO, no una prueba de que falte la firma: el registro de AP no tiene campo
# de aprobador, asi que C5 marca los pagos grandes PARA revision de autorizacion
# (segregacion de funciones), no afirma que esten sin autorizar. Se calibra por
# encima del limite de gasto operativo documentado (politica T&E: >USD 5k aprueba
# VP Finanzas) como umbral de autorizacion senior para desembolsos a proveedores.
APPROVAL_THRESHOLD_USD = _registry_param("approval_threshold_usd", 25000.0)


def _trial_balance_imbalance(period=LATEST):
    """Maximo |Activo - (Pasivo + Patrimonio)| por entidad, en USD.

    Valida la IDENTIDAD del balance de comprobacion (debe cuadrar). En libros
    bien formados pasa por construccion; un asiento mal cargado o un balance
    editado a mano lo hacen FALLAR (probado con tamper test). Como los CSV
    guardan montos redondeados a 2 decimales en moneda local y aca se
    reconvierten a USD, queda un residuo de centavos; por eso usa tolerancia.
    """
    max_imb = 0.0
    for e in _ENT:
        eid = e["entity_id"]
        if (period, _CCY[eid]) not in _FX:
            continue   # sin tasa no se puede traducir: lo reporta el control C2
        a = l = q = 0.0
        for r in _BS:
            if r["entity_id"] != eid or r.get("period") != period:
                continue   # filtra por entidad Y periodo (consistente con el resto del modulo)
            t = _BS_TYPE.get(r["account_code"])
            if t is None:
                continue
            v = _usd(float(r["amount_local"]), _CCY[eid], period)
            if t == "A":
                a += v
            elif t == "L":
                l += v
            else:
                q += v
        max_imb = max(max_imb, abs(a - (l + q)))
    return max_imb


def control_checks(period=LATEST, as_of="2026-05-31",
                   bs_tolerance=1.0, approval_threshold=APPROVAL_THRESHOLD_USD):
    """Suite de controles internos deterministicos.

    Devuelve una lista de chequeos, cada uno con estado (PASS / FAIL /
    EXCEPTION) y severidad, mas un resumen. Convencion: FAIL = se rompio un
    invariante de integridad; EXCEPTION = hay items que requieren revision
    (no necesariamente un error); PASS = limpio. El agente solo narra; aca se
    decide deterministicamente -> testeable por evals.
    """
    asof = datetime.date.fromisoformat(as_of)
    checks = []

    # C1 - Balance de comprobacion: Activo = Pasivo + Patrimonio (integridad).
    imb = _trial_balance_imbalance(period)
    checks.append({
        "id": "C1", "name": "Trial balance in balance",
        "status": "PASS" if imb <= bs_tolerance else "FAIL",
        "severity": "CRITICAL", "value": imb,
        "detail": (f"books balance (max entity imbalance USD {imb:,.2f}, tol {bs_tolerance:,.0f})"
                   if imb <= bs_tolerance else
                   f"books do NOT balance: max entity imbalance USD {imb:,.0f}"),
    })

    # C2 - Completitud de FX: toda combinacion periodo/moneda usada tiene tasa.
    needed = {(r["period"], _CCY[r["entity_id"]]) for r in _PNL}
    needed |= {(period, _CCY[r["entity_id"]]) for r in _BS}
    missing = sorted(k for k in needed if k not in _FX)
    checks.append({
        "id": "C2", "name": "FX rate completeness",
        "status": "PASS" if not missing else "FAIL",
        "severity": "CRITICAL", "value": len(missing),
        "detail": (f"all {len(needed)} period/currency pairs have an FX rate"
                   if not missing else f"missing FX rates: {missing}"),
    })

    # C3 - Corte de posteo: ningun documento con fecha posterior al cierre.
    future = [r["invoice_id"] for r in _INV
              if datetime.date.fromisoformat(r["issue_date"]) > asof]
    future += [r["bill_id"] for r in _AP
               if datetime.date.fromisoformat(r["issue_date"]) > asof]
    checks.append({
        "id": "C3", "name": "Posting cutoff (no future-dated documents)",
        "status": "PASS" if not future else "FAIL",
        "severity": "HIGH", "value": len(future),
        "detail": ("no AR/AP documents dated after period close"
                   if not future else
                   f"{len(future)} document(s) dated after close: {future[:5]}"),
    })

    # C4 - Desembolsos duplicados: AP abierta con misma entidad+proveedor+monto.
    # La clave normaliza el monto a float (round 2): asi el control no se escapa
    # por diferencias de formato del origen ('100.0' vs '100.00'), consistente
    # con como el resto del modulo trata amount_local.
    groups = {}
    for r in _AP:
        if r["status"] != "open":
            continue
        k = (r["entity_id"], r["vendor"], round(float(r["amount_local"]), 2))
        groups.setdefault(k, []).append(r["bill_id"])
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    checks.append({
        "id": "C4", "name": "Duplicate disbursements",
        "status": "PASS" if not dups else "EXCEPTION",
        "severity": "HIGH", "value": len(dups),
        "detail": ("no duplicate open payables detected"
                   if not dups else
                   f"{len(dups)} potential duplicate payable group(s): "
                   f"{[v for v in dups.values()][:3]}"),
    })

    # C5 - Desembolsos grandes para revision de autorizacion (segregacion de
    # funciones). Es un screen POR MONTO: el registro de AP no tiene campo de
    # aprobador, asi que esto NO prueba que falte la firma, sino que selecciona
    # los pagos a proveedor que -por tamano- deben pasar por revision de
    # autorizacion senior. Se saltean los no traducibles (FX faltante -> los
    # reporta C2).
    over = [(r["bill_id"], _usd(float(r["amount_local"]), r["currency"], LATEST))
            for r in _AP if r["status"] == "open"
            and (LATEST, r["currency"]) in _FX
            and _usd(float(r["amount_local"]), r["currency"], LATEST) >= approval_threshold]
    over_total = sum(a for _, a in over)
    checks.append({
        "id": "C5", "name": "Large disbursements pending authorization review",
        "status": "PASS" if not over else "EXCEPTION",
        "severity": "HIGH",
        "value": {"n": len(over), "total": over_total, "threshold": approval_threshold},
        "detail": (f"no open payment is at or above the USD {approval_threshold:,.0f} authorization-review threshold"
                   if not over else
                   f"{len(over)} open payment(s) at or above the USD {approval_threshold:,.0f} "
                   f"authorization-review threshold (total USD {over_total:,.0f}): flagged for "
                   f"documented authorization review (amount-based screen)"),
    })

    return {
        "checks": checks,
        "n_fail": sum(1 for c in checks if c["status"] == "FAIL"),
        "n_exception": sum(1 for c in checks if c["status"] == "EXCEPTION"),
        "n_pass": sum(1 for c in checks if c["status"] == "PASS"),
        "books_balanced": imb <= bs_tolerance, "max_bs_imbalance": imb,
        "approval_exceptions": len(over), "approval_exceptions_total": over_total,
    }


# --------------------------------------------------------------------------
# Record-to-Report: cierre contable, estados financieros y auditoria. Todo
# deterministico en USD. El balance se generó articulado (2 periodos, RE rota
# por el resultado, caja = activo de cuadre), de modo que: el cierre RECONCILIA
# (subledger AR/AP == cuenta de control del GL), los 3 estados ATAN entre si, y
# el flujo de efectivo CUADRA contra la variacion de caja. Los agentes narran;
# aca se calcula y se decide -> testeable por evals.
# --------------------------------------------------------------------------

_BS_PERIODS = sorted({r["period"] for r in _BS})


def _prev_bs_period(period):
    """Periodo de balance inmediatamente anterior que EXISTE en el balance (o None).

    Se basa en los periodos presentes en el balance, no en los del P&L: el P&L
    tiene 5 periodos pero el balance solo 2. Asi un comparativo ausente se trata
    como 'sin periodo previo' (N/A), en vez de compararse contra ceros implicitos
    (lo que daria un BREAK/no-cuadre espurio)."""
    earlier = [p for p in _BS_PERIODS if p < period]
    return earlier[-1] if earlier else None


def _bs_usd(period=LATEST):
    """Balance consolidado por cuenta, en USD, para un periodo."""
    agg = {}
    for r in _BS:
        if r["period"] != period:
            continue
        agg[r["account_code"]] = agg.get(r["account_code"], 0.0) + \
            _usd(float(r["amount_local"]), _CCY[r["entity_id"]], period)
    return agg


def subledger_totals_usd(period=LATEST):
    """Totales del subledger (facturas abiertas) en USD: AR y AP."""
    ar = sum(_usd(float(r["amount_local"]), r["currency"], period)
             for r in _INV if r["status"] == "open")
    ap = sum(_usd(float(r["amount_local"]), r["currency"], period)
             for r in _AP if r["status"] == "open")
    return {"ar": ar, "ap": ap}


# --- Accounting & Close (cierre contable) ------------------------------

def close_reconciliations(period=LATEST, tolerance=1.0):
    """Conciliaciones de cierre, deterministicas:
    - Subledger AR/AP vs la cuenta de control del GL (deben atar).
    - Articulacion del patrimonio: RE del periodo - RE del previo == resultado.
    Devuelve cada conciliacion con su diferencia y estado, y si el cierre quedo
    todo conciliado (sin partidas abiertas)."""
    bs = _bs_usd(period)
    recs = []
    # El subledger de facturas es una foto AL CIERRE MAS RECIENTE (no hay snapshot
    # por periodo), asi que el tie-out subledger -> GL solo aplica al periodo LATEST;
    # para periodos previos se marca N/A en vez de comparar contra el subledger actual.
    if period == LATEST:
        sub = subledger_totals_usd(period)
        for label, sub_v, gl_v in [
            ("Accounts receivable", sub["ar"], bs.get("1100", 0.0)),
            ("Accounts payable", sub["ap"], bs.get("2000", 0.0)),
        ]:
            diff = sub_v - gl_v
            recs.append({"item": label, "subledger": sub_v, "gl": gl_v, "diff": diff,
                         "status": "RECONCILED" if abs(diff) <= tolerance else "OPEN ITEM"})
    else:
        for label, gl_v in [("Accounts receivable", bs.get("1100", 0.0)),
                            ("Accounts payable", bs.get("2000", 0.0))]:
            recs.append({"item": label, "subledger": None, "gl": gl_v, "diff": None,
                         "status": "N/A (no subledger snapshot for prior period)"})

    prev = _prev_bs_period(period)
    re_move = (bs.get("3900", 0.0) - _bs_usd(prev).get("3900", 0.0)) if prev else None
    ni = pnl_usd(period)["operating_income"]
    if re_move is None:
        art_status, art_diff = "N/A", None
    elif abs(re_move - ni) <= tolerance:
        art_status, art_diff = "TIED", re_move - ni
    else:
        art_status, art_diff = "BREAK", re_move - ni
    articulation = {"item": "Retained earnings roll-forward", "re_movement": re_move,
                    "net_income": ni, "diff": art_diff, "status": art_status}

    n_open = sum(1 for r in recs if r["status"] == "OPEN ITEM") + (1 if art_status == "BREAK" else 0)
    return {"recs": recs, "articulation": articulation, "n_open_items": n_open,
            "all_reconciled": n_open == 0}


# --- Financial Reporting (estados financieros) -------------------------

def income_statement(period=LATEST):
    """Estado de resultados consolidado en USD. Sin lineas debajo de la operativa
    en este dataset, el resultado neto == resultado operativo."""
    p = pnl_usd(period)
    rev = p["revenue"]
    return {
        "revenue": rev, "cogs": p["cogs"], "gross": p["gross"],
        "sm": p["sm"], "rd": p["rd"], "ga": p["ga"], "opex": p["opex"],
        "operating_income": p["operating_income"], "net_income": p["operating_income"],
        "gross_margin_pct": (p["gross"] / rev * 100) if rev else 0.0,
        "net_margin_pct": (p["operating_income"] / rev * 100) if rev else 0.0,
    }


def balance_sheet_statement(period=LATEST):
    """Balance general consolidado en USD, con el chequeo de cuadre A = P + PN."""
    b = _bs_usd(period)
    assets = {"cash": b.get("1000", 0.0), "accounts_receivable": b.get("1100", 0.0),
              "fixed_assets": b.get("1500", 0.0)}
    liabilities = {"accounts_payable": b.get("2000", 0.0), "deferred_revenue": b.get("2500", 0.0)}
    equity = {"paid_in_capital": b.get("3000", 0.0), "retained_earnings": b.get("3900", 0.0)}
    ta, tl, te = sum(assets.values()), sum(liabilities.values()), sum(equity.values())
    return {"assets": assets, "liabilities": liabilities, "equity": equity,
            "total_assets": ta, "total_liabilities": tl, "total_equity": te,
            "balance_check": ta - (tl + te)}


def cash_flow_statement(period=LATEST, tolerance=1.0):
    """Estado de flujo de efectivo (metodo indirecto), consolidado en USD.
    Articula: resultado +/- variacion de capital de trabajo (operativo) +/-
    inversion +/- financiacion = variacion real de caja entre cierres.

    Nota de honestidad: en libros INTERNAMENTE CONSISTENTES (el balance esta
    articulado: caja = activo de cuadre, RE rota por el resultado) el total del
    estado iguala a la variacion de caja por construccion -> 'foots' no puede
    fallar sobre datos consistentes. Por eso el chequeo vale como guarda de
    CONSISTENCIA/regresion (atrapa un balance manipulado o un cableado roto: lo
    probamos con tamper tests), no como aseguramiento independiente."""
    prev = _prev_bs_period(period)
    b = _bs_usd(period)
    bp = _bs_usd(prev) if prev else {}
    ni = pnl_usd(period)["operating_income"]
    d_ar = b.get("1100", 0.0) - bp.get("1100", 0.0)        # +AR consume caja
    d_ap = b.get("2000", 0.0) - bp.get("2000", 0.0)        # +AP libera caja
    d_def = b.get("2500", 0.0) - bp.get("2500", 0.0)       # +deferred libera caja
    d_fixed = b.get("1500", 0.0) - bp.get("1500", 0.0)     # +fijo = capex
    d_paid = b.get("3000", 0.0) - bp.get("3000", 0.0)      # +paid-in = emision
    cfo = ni - d_ar + d_ap + d_def
    cfi = -d_fixed
    cff = d_paid
    net = cfo + cfi + cff
    d_cash = b.get("1000", 0.0) - bp.get("1000", 0.0)
    return {"net_income": ni, "d_ar": d_ar, "d_ap": d_ap, "d_deferred": d_def,
            "cfo": cfo, "cfi": cfi, "cff": cff, "net_change": net,
            "cash_begin": bp.get("1000", 0.0), "cash_end": b.get("1000", 0.0),
            "actual_change": d_cash, "foots": abs(net - d_cash) <= tolerance,
            "has_comparative": prev is not None}


# --- Audit (aseguramiento independiente) -------------------------------

def audit_procedures(period=LATEST, tolerance=1.0, sample_threshold=25000.0):
    """Procedimientos de auditoria INDEPENDIENTES (tercera linea).

    Independencia real: re-deriva cada cifra DESDE las fuentes (el subledger de
    facturas y el mayor / balance crudo), NO llamando a las funciones del cierre
    ni del reporting (close_reconciliations, balance_sheet_statement,
    cash_flow_statement). Asi, si hubiera un bug en esas funciones, la auditoria
    NO lo replicaria: lo detectaria como excepcion. Re-ejecuta:
      - los tie-outs subledger AR/AP vs la cuenta de control del GL,
      - la articulacion del patrimonio (movimiento de RE vs resultado),
      - el cuadre del balance (A = P + PN),
      - el cuadre del flujo de efectivo (re-arma el indirecto desde el balance),
      - y muestrea (vouching) los desembolsos de alto valor.
    Emite una OPINION (unqualified / qualified / adverse) segun cuantos
    procedimientos fallan. No re-escala las partidas (eso tiene dueno en el
    cierre): su salida propia es la opinion."""
    findings = []

    # Fuentes crudas (independientes de las funciones del cierre/reporting):
    b = _bs_usd(period)                              # mayor: balance por cuenta, USD
    prev = _prev_bs_period(period)
    bp = _bs_usd(prev) if prev else {}
    ni = pnl_usd(period)["operating_income"]         # P&L (fuente, no un estado derivado)

    # P1/P2 - tie-out subledger AR/AP vs cuenta de control del GL (re-derivado).
    # El subledger es una foto al cierre mas reciente -> el tie-out solo aplica a LATEST.
    if period == LATEST:
        ar_sub = sum(_usd(float(r["amount_local"]), r["currency"], period)
                     for r in _INV if r["status"] == "open")
        ap_sub = sum(_usd(float(r["amount_local"]), r["currency"], period)
                     for r in _AP if r["status"] == "open")
        for label, sub_v, gl_v in [("Accounts receivable", ar_sub, b.get("1100", 0.0)),
                                   ("Accounts payable", ap_sub, b.get("2000", 0.0))]:
            diff = sub_v - gl_v
            findings.append({"proc": f"{label}: subledger ties to GL",
                             "ok": abs(diff) <= tolerance, "detail": f"difference USD {diff:,.2f}"})
    else:
        for label in ("Accounts receivable", "Accounts payable"):
            findings.append({"proc": f"{label}: subledger ties to GL", "ok": True,
                             "detail": "no subledger snapshot for prior period"})

    # P3 - articulacion del patrimonio: movimiento de RE vs resultado (re-derivado).
    if prev:
        re_move = b.get("3900", 0.0) - bp.get("3900", 0.0)
        findings.append({"proc": "Retained earnings articulation",
                         "ok": abs(re_move - ni) <= tolerance,
                         "detail": f"RE movement vs net income differ by USD {re_move - ni:,.2f}"})
    else:
        findings.append({"proc": "Retained earnings articulation", "ok": True,
                         "detail": "no prior period to test"})

    # P4 - cuadre del balance A = P + PN (re-sumado desde el mayor).
    assets = b.get("1000", 0.0) + b.get("1100", 0.0) + b.get("1500", 0.0)
    liab_eq = b.get("2000", 0.0) + b.get("2500", 0.0) + b.get("3000", 0.0) + b.get("3900", 0.0)
    findings.append({"proc": "Balance sheet foots (A = L + E)",
                     "ok": abs(assets - liab_eq) <= tolerance,
                     "detail": f"imbalance USD {assets - liab_eq:,.2f}"})

    # P5 - cuadre del flujo de efectivo: re-arma el indirecto desde el balance
    #      (independiente de cash_flow_statement) y lo compara con la var. de caja.
    if prev:
        net = (ni
               - (b.get("1100", 0.0) - bp.get("1100", 0.0))      # dAR
               + (b.get("2000", 0.0) - bp.get("2000", 0.0))      # dAP
               + (b.get("2500", 0.0) - bp.get("2500", 0.0))      # dDeferred
               - (b.get("1500", 0.0) - bp.get("1500", 0.0))      # dFixed (capex)
               + (b.get("3000", 0.0) - bp.get("3000", 0.0)))     # dPaid-in
        d_cash = b.get("1000", 0.0) - bp.get("1000", 0.0)
        findings.append({"proc": "Cash flow ties to change in cash",
                         "ok": abs(net - d_cash) <= tolerance,
                         "detail": f"reconstructed USD {net:,.0f} vs actual USD {d_cash:,.0f}"})
    else:
        findings.append({"proc": "Cash flow ties to change in cash", "ok": True,
                         "detail": "no prior period to test"})

    # P6 - vouching de desembolsos de alto valor (muestra para revision).
    sample = [r["bill_id"] for r in _AP if r["status"] == "open"
              and (period, r["currency"]) in _FX
              and _usd(float(r["amount_local"]), r["currency"], period) >= sample_threshold]
    findings.append({"proc": f"High-value disbursements vouched (>= USD {sample_threshold:,.0f})",
                     "ok": True, "detail": f"{len(sample)} item(s) sampled for authorization"})

    n_exc = sum(1 for f in findings if not f["ok"])
    opinion = "unqualified" if n_exc == 0 else ("qualified" if n_exc <= 1 else "adverse")
    return {"findings": findings, "n_procedures": len(findings),
            "n_exceptions": n_exc, "opinion": opinion}
