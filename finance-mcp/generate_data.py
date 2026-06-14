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

# ---- balance_sheet.csv (snapshot al ultimo periodo, moneda local) ----
# Calculamos cash, AR, FA, AP, deferred, paid-in; RE es el plug.
last = periods[-1]
bs_rows = []
for eid,name,country,cur,scale in entities:
    rate = fx[cur][-1]
    # montos en USD, luego a local
    paid_in = 9000000 * scale          # rondas de equity
    fixed   = 350000 * scale
    # AR ~ 1.8 meses de revenue del ultimo mes
    last_rev_usd = 380000*scale*(1.06**4)
    ar      = last_rev_usd * 1.8
    deferred= last_rev_usd * 2.2       # SaaS: ingresos diferidos altos
    ap      = last_rev_usd * 0.6
    # deficit acumulado aprox: quema mensual * meses de vida
    monthly_burn = last_rev_usd * 0.95
    accum_deficit = monthly_burn * 14
    # cash es el plug de liquidez: equity - deficit + working capital neto + fixed
    cash = paid_in - accum_deficit - fixed + (deferred - ar + ap)
    vals_usd = {
        "1000": cash, "1100": ar, "1500": fixed,
        "2000": ap, "2500": deferred, "3000": paid_in,
    }
    # RE plug para que Activo = Pasivo + Patrimonio
    assets = vals_usd["1000"]+vals_usd["1100"]+vals_usd["1500"]
    liab_eq = vals_usd["2000"]+vals_usd["2500"]+vals_usd["3000"]
    re = assets - liab_eq   # signo: equity normal credito; RE negativo = deficit
    vals_usd["3900"] = re
    for code, v in vals_usd.items():
        bs_rows.append([eid, last, code, round(v*rate, 2)])

with open(f"{DATA}/balance_sheet.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["entity_id","period","account_code","amount_local"])
    w.writerows(bs_rows)

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

print("Generado en", DATA)
for fn in sorted(os.listdir(DATA)):
    print(" ", fn)
