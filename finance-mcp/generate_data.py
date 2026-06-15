"""
generate_data.py - Genera datos sinteticos coherentes para Lumen Inc.,
una SaaS B2B post-seed que opera en 6 paises. Salida: CSVs en data/.

Decisiones de modelo:
- Convencion con signo: activos y gastos positivos (debito);
  pasivos, patrimonio e ingresos positivos en su lado natural (credito).
- El balance cuadra por entidad usando Retained Earnings como plug
  (practica estandar). En una post-seed da negativo: deficit acumulado.
- FX a tasa de cierre de periodo (congelada), no en vivo.
"""

import csv, os, random

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

periods = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]

# entity_id, name, country, currency, scale (tamano relativo)
entities = [
    ("US", "Lumen Inc.",        "United States", "USD", 1.00),
    ("UK", "Lumen UK Ltd.",     "United Kingdom","GBP", 0.45),
    ("DE", "Lumen GmbH",        "Germany",       "EUR", 0.40),
    ("BR", "Lumen Brasil Ltda", "Brazil",        "BRL", 0.30),
    ("AR", "Lumen Argentina SA","Argentina",     "ARS", 0.18),
    ("IN", "Lumen India Pvt",   "India",         "INR", 0.25),
]

# FX: unidades de moneda local por 1 USD, al cierre de cada periodo.
fx = {
    "USD": [1, 1, 1, 1, 1],
    "GBP": [0.79, 0.78, 0.78, 0.77, 0.75],
    "EUR": [0.92, 0.91, 0.90, 0.88, 0.86],
    "BRL": [4.95, 5.00, 5.05, 5.08, 5.11],
    "ARS": [1050, 1090, 1130, 1180, 1240],
    "INR": [83.5, 84.0, 84.6, 85.0, 95.1],
}

coa = [
    ("1000", "Cash and equivalents", "Asset"),
    ("1100", "Accounts receivable",  "Asset"),
    ("1500", "Fixed assets, net",    "Asset"),
    ("2000", "Accounts payable",     "Liability"),
    ("2500", "Deferred revenue",     "Liability"),
    ("3000", "Paid-in capital",      "Equity"),
    ("3900", "Retained earnings",    "Equity"),
    ("4000", "Revenue",              "Revenue"),
    ("5000", "Cost of revenue",      "Expense"),
    ("6000", "Sales & marketing",    "Expense"),
    ("6100", "Research & development","Expense"),
    ("6200", "General & admin",      "Expense"),
]

# ---- entities.csv ----
with open(f"{DATA}/entities.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","name","country","currency"])
    for eid,name,country,cur,_ in entities:
        w.writerow([eid,name,country,cur])

# ---- chart_of_accounts.csv ----
with open(f"{DATA}/chart_of_accounts.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["account_code","account_name","type"])
    for code,name,typ in coa: w.writerow([code,name,typ])

# ---- fx_rates.csv ----
with open(f"{DATA}/fx_rates.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["period","currency","units_per_usd"])
    for cur, rates in fx.items():
        for p, r in zip(periods, rates):
            w.writerow([p, cur, r])

# ---- pnl_activity.csv (actividad mensual de P&L, en moneda local) ----
# Base mensual en USD por entidad (escala). SaaS que crece ~6%/mes.
pnl_rows = []
pnl_by = {}   # P&L en USD por (entidad, periodo): para articular el balance (RE = RE_prev + NI)
for eid,name,country,cur,scale in entities:
    base_rev_usd = 380000 * scale
    for i, p in enumerate(periods):
        growth = (1.06 ** i)
        rev_usd  = base_rev_usd * growth * random.uniform(0.97, 1.03)
        # margenes tipicos SaaS post-seed (quema caja)
        cogs = rev_usd * random.uniform(0.20, 0.24)      # ~78% gross margin
        sm   = rev_usd * random.uniform(0.55, 0.65)      # gasto comercial alto
        rd   = rev_usd * random.uniform(0.45, 0.55)
        ga   = rev_usd * random.uniform(0.25, 0.32)
        pnl_by[(eid, p)] = {"rev": rev_usd, "cogs": cogs, "sm": sm, "rd": rd, "ga": ga,
                            "ni": rev_usd - cogs - sm - rd - ga}
        rate = fx[cur][i]
        for code, val_usd in [("4000",rev_usd),("5000",cogs),
                              ("6000",sm),("6100",rd),("6200",ga)]:
            pnl_rows.append([eid, p, code, round(val_usd*rate, 2)])

with open(f"{DATA}/pnl_activity.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","period","account_code","amount_local"])
    w.writerows(pnl_rows)

# ---- budget.csv (plan operativo, en USD, por entidad/periodo/cuenta) ----
# El presupuesto es un PLAN deliberado fijado antes del periodo, NO los actuals
# con ruido (eso seria circular). Por eso es deterministico: crecimiento
# planificado limpio de 6%/mes y ratios objetivo de margen. La desviacion de
# los actuals contra este plan es la varianza (el objeto del analisis FP&A).
# Se guarda en USD: es el plan del grupo en su moneda funcional. Como los
# actuals se convierten a USD a la tasa del mismo periodo, la varianza
# consolidada en USD queda puramente operativa (sin efecto de traduccion FX).
plan_ratios = {        # como % del revenue planificado
    "5000": 0.22,      # Cost of revenue       -> gross margin objetivo 78%
    "6000": 0.58,      # Sales & marketing
    "6100": 0.50,      # Research & development
    "6200": 0.28,      # General & admin
}
budget_rows = []
for eid, name, country, cur, scale in entities:
    base_rev_usd = 380000 * scale
    for i, p in enumerate(periods):
        rev_plan = base_rev_usd * (1.06 ** i)   # crecimiento planificado, sin ruido
        budget_rows.append([eid, p, "4000", round(rev_plan, 2)])
        for code, ratio in plan_ratios.items():
            budget_rows.append([eid, p, code, round(rev_plan * ratio, 2)])

with open(f"{DATA}/budget.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","period","account_code","amount_usd"])
    w.writerows(budget_rows)

last = periods[-1]          # ultimo periodo de cierre (lo usan tax y el balance)
prev = periods[-2]          # periodo previo (para el estado de flujo de efectivo)
# El balance se genera MAS ABAJO (despues de las facturas) para que las cuentas
# de control (AR/AP) aten exactamente al subledger. Ver bloque balance_sheet.csv.

# ---- ar_invoices.csv (facturas para aging) ----
import datetime
asof = datetime.date(2026,5,31)
buckets_target = [10, 8, 5, 4, 3]  # cant facturas por entidad por tramo aprox
inv_rows = []
inv_id = 1000
customers = ["Northwind","Globex","Initech","Umbrella","Stark","Wayne","Acme","Hooli","Soylent","Vandelay"]
for eid,name,country,cur,scale in entities:
    rate = fx[cur][-1]
    last_rev_usd = 380000*scale*(1.06**4)
    n = random.randint(12, 20)
    for _ in range(n):
        inv_id += 1
        days_old = random.choice([random.randint(0,25), random.randint(0,25),
                                  random.randint(35,55), random.randint(65,85),
                                  random.randint(95,150)])
        issue = asof - datetime.timedelta(days=days_old+30)
        due = issue + datetime.timedelta(days=30)
        amt_usd = last_rev_usd * random.uniform(0.03, 0.15)
        paid = random.random() < 0.35
        inv_rows.append([f"INV-{inv_id}", eid, random.choice(customers), cur,
                         round(amt_usd*rate,2), issue.isoformat(), due.isoformat(),
                         "paid" if paid else "open"])

with open(f"{DATA}/ar_invoices.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["invoice_id","entity_id","customer","currency","amount_local","issue_date","due_date","status"])
    w.writerows(inv_rows)

# ---- ap_invoices.csv (facturas de proveedores, para AP / payables) ----
# Misma estructura que AR pero del lado de pagar. Mezcla de vencidas y por
# vencer, en moneda local. Sirve para DPO, vencidos y vencimientos proximos.
vendors = ["AWS","Stripe","Salesforce","WeWork","Deloitte","LinkedIn",
           "Google Cloud","Datadog","Cuatrecasas","Marsh"]
ap_rows = []
ap_id = 5000
for eid, name, country, cur, scale in entities:
    rate = fx[cur][-1]
    last_rev_usd = 380000*scale*(1.06**4)
    n = random.randint(8, 14)
    for _ in range(n):
        ap_id += 1
        days_old = random.choice([random.randint(0,20), random.randint(0,20),
                                  random.randint(35,55), random.randint(65,90)])
        issue = asof - datetime.timedelta(days=days_old+30)
        due = issue + datetime.timedelta(days=30)
        amt_usd = last_rev_usd * random.uniform(0.02, 0.10)
        paid = random.random() < 0.45
        ap_rows.append([f"BILL-{ap_id}", eid, random.choice(vendors), cur,
                        round(amt_usd*rate,2), issue.isoformat(), due.isoformat(),
                        "paid" if paid else "open"])

with open(f"{DATA}/ap_invoices.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["bill_id","entity_id","vendor","currency","amount_local","issue_date","due_date","status"])
    w.writerows(ap_rows)

# ---- tax_obligations.csv (obligaciones impositivas por entidad/jurisdiccion) ----
# Cada entidad tiene IVA, impuesto a las ganancias (provision) y retenciones,
# con vencimientos alrededor del cierre. Algunas pendientes, algunas vencidas.
tax_rows = []
for eid, name, country, cur, scale in entities:
    rate = fx[cur][-1]
    mrev = 380000*scale*(1.06**4)
    obligations = [
        ("VAT",                 mrev*0.21,  random.choice([-6, 9, 17])),
        ("Income tax (prov.)",  mrev*0.05,  random.choice([24, 38])),
        ("Payroll withholding", mrev*0.08,  random.choice([-4, 11])),
    ]
    for ttype, amt_usd, due_offset in obligations:
        due = asof + datetime.timedelta(days=due_offset)
        status = "paid" if random.random() < 0.40 else "pending"
        tax_rows.append([eid, country, ttype, last, round(amt_usd*rate, 2), cur,
                         due.isoformat(), status])

with open(f"{DATA}/tax_obligations.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","jurisdiction","tax_type","period","amount_local","currency","due_date","status"])
    w.writerows(tax_rows)

# ---- balance_sheet.csv (2 periodos, ARTICULADO) ----
# Se genera al final, despues de las facturas, por dos razones de integridad:
#  1) Las cuentas de control AR/AP atan EXACTAMENTE al subledger (suma de
#     facturas abiertas) -> el cierre reconcilia limpio (sin diferencia).
#  2) Dos periodos (prev y last) con patrimonio que ROTA por el resultado
#     (RE_last = RE_prev + NI_last) y la CAJA como activo de cuadre. Asi los
#     tres estados articulan: el flujo de efectivo indirecto cuadra contra la
#     variacion real de caja (NI +/- variacion de capital de trabajo = dCaja).
ar_open, ap_open = {}, {}
for r in inv_rows:                       # inv_rows: [id, eid, cust, cur, amt_local, issue, due, status]
    if r[7] == "open":
        ar_open[r[1]] = ar_open.get(r[1], 0.0) + r[4]
for r in ap_rows:                        # ap_rows: [id, eid, vendor, cur, amt_local, issue, due, status]
    if r[7] == "open":
        ap_open[r[1]] = ap_open.get(r[1], 0.0) + r[4]

DEFICIT_MONTHS = 13.0                     # deficit acumulado al cierre previo (post-seed, quema caja)
bs_rows = []
for eid, name, country, cur, scale in entities:
    rate04, rate05 = fx[cur][3], fx[cur][4]
    rev04 = pnl_by[(eid, prev)]["rev"]
    rev05 = pnl_by[(eid, last)]["rev"]
    ni05  = pnl_by[(eid, last)]["ni"]
    paid_in = 9_000_000 * scale          # rondas de equity (sin emision en el mes -> CFF=0)
    fixed   = 350_000 * scale            # activo fijo (sin capex en el mes -> CFI=0)
    deferred05, deferred04 = rev05 * 2.2, rev04 * 2.2     # SaaS: ingresos diferidos altos
    ar05 = ar_open.get(eid, 0.0) / rate05    # = subledger AR abierto, en USD (ata exacto)
    ap05 = ap_open.get(eid, 0.0) / rate05    # = subledger AP abierto, en USD (ata exacto)
    g = (rev04 / rev05) if rev05 else 1.0    # capital de trabajo previo, escalado por crecimiento
    ar04, ap04 = ar05 * g, ap05 * g
    re04 = -(rev05 * DEFICIT_MONTHS)     # deficit acumulado al cierre previo
    re05 = re04 + ni05                   # ARTICULACION: el patrimonio rota por el resultado del mes
    cash04 = (ap04 + deferred04 + paid_in + re04) - (ar04 + fixed)   # caja = activo de cuadre
    cash05 = (ap05 + deferred05 + paid_in + re05) - (ar05 + fixed)
    for p, rate, ar, ap, deferred, re, cash in [
        (prev, rate04, ar04, ap04, deferred04, re04, cash04),
        (last, rate05, ar05, ap05, deferred05, re05, cash05),
    ]:
        vals_usd = {"1000": cash, "1100": ar, "1500": fixed,
                    "2000": ap, "2500": deferred, "3000": paid_in, "3900": re}
        for code, v in vals_usd.items():
            bs_rows.append([eid, p, code, round(v * rate, 2)])

with open(f"{DATA}/balance_sheet.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","period","account_code","amount_local"])
    w.writerows(bs_rows)

print("Generado en", DATA)
for fn in sorted(os.listdir(DATA)):
    print(" ", fn)
