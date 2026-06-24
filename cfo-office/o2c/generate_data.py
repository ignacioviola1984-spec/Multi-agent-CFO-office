"""
generate_data.py - Deterministic synthetic O2C / Order-to-Cash dataset generator.

Builds 15 coherent CSVs with relational integrity across the full chain:
  CRM opportunity -> contract -> sales order -> billing schedule -> invoice
  -> revenue schedule / deferred revenue ; invoice -> payment -> bank receipt
  -> cash application ; invoice -> credit memo / collections / disputes ;
  customer -> credit limit.

Multi-entity, multi-region (NA / EMEA / LATAM), multi-currency
(USD, EUR, GBP, BRL, MXN, ARS). The data is mostly clean but seeds a KNOWN
number of each exception type (see SEEDED at the end), so the controls have a
ground truth to catch and the tests can assert exactly those breaks.

Deterministic: fixed seed, no wall-clock, no network. Re-running reproduces the
identical files. Run:  python cfo-office/o2c/generate_data.py
"""

import csv
import os
import random
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import o2c_policy as P  # noqa: E402

SEED = 7
rng = random.Random(SEED)
DATA = os.path.join(HERE, "data")
AS_OF = date(2026, 5, 31)          # reporting as-of date (period 2026-05)
FX = P.FX_TO_USD

SEEDED = {}                        # manifest of intentionally-seeded exceptions


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def add_months(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    leap = (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0))
    dim = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, dim))


def iso(d):
    return d.isoformat()


def month_str(d):
    return f"{d.year:04d}-{d.month:02d}"


def to_local(amount_usd, ccy):
    """Convert a USD-equivalent magnitude into the local currency amount stored
    on the record (so ARS/BRL records carry realistically large numbers)."""
    return round(amount_usd / FX[ccy], 2)


def wchoice(pairs):
    """Weighted choice from [(value, weight), ...]."""
    vals, wts = zip(*pairs)
    return rng.choices(vals, weights=wts, k=1)[0]


def terms_days(terms):
    return {"NET15": 15, "NET30": 30, "NET45": 45, "NET60": 60, "NET90": 90}.get(terms, 30)


def write_csv(name, rows, columns):
    path = os.path.join(DATA, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    return len(rows)


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
GEO = [
    # region, country, currency, legal_entity, tax_profile, weight
    ("NA", "US", "USD", "Acme US Inc", "US-Sales-Tax", 45),
    ("EMEA", "GB", "GBP", "Acme UK Ltd", "UK-VAT", 12),
    ("EMEA", "DE", "EUR", "Acme EU GmbH", "EU-VAT", 10),
    ("EMEA", "ES", "EUR", "Acme EU GmbH", "EU-VAT", 8),
    ("LATAM", "AR", "ARS", "Acme Argentina SA", "AR-IVA", 9),
    ("LATAM", "BR", "BRL", "Acme Brasil Ltda", "BR-ICMS", 9),
    ("LATAM", "MX", "MXN", "Acme Mexico SA", "MX-IVA", 7),
]
TAX_RATE = {"US-Sales-Tax": 0.00, "UK-VAT": 0.20, "EU-VAT": 0.20,
            "AR-IVA": 0.21, "BR-ICMS": 0.17, "MX-IVA": 0.16, "Exempt": 0.00}

SEGMENTS = [("SMB", 45), ("Mid-Market", 30), ("Enterprise", 18), ("Strategic", 7)]
ARR_BAND = {"SMB": (5_000, 40_000), "Mid-Market": (40_000, 180_000),
            "Enterprise": (180_000, 900_000), "Strategic": (700_000, 3_500_000)}
PRODUCTS = [
    ("SaaS Subscription", "subscription", "4000-Subscription-Rev"),
    ("Usage-Based", "usage", "4100-Usage-Rev"),
    ("Professional Services", "services", "4200-Services-Rev"),
    ("Implementation", "implementation", "4300-Implementation-Rev"),
]
REC_METHOD = {"subscription": "ratable", "usage": "point_in_time",
              "services": "as_delivered", "implementation": "point_in_time"}
# (value, selection weight) - monthly dominates, like a real SaaS book.
FREQS = [("monthly", 55), ("quarterly", 30), ("annual", 15)]
FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}
NAMES_A = ["Northwind", "Brightwater", "Cedar", "Summit", "Vertex", "Harbor", "Lumen",
           "Atlas", "Granite", "Pioneer", "Cobalt", "Meridian", "Solstice", "Falcon",
           "Riverstone", "Ironclad", "Beacon", "Cascade", "Orchard", "Keystone",
           "Maple", "Sterling", "Quantum", "Aurora", "Tideline", "Foundry", "Halcyon"]
NAMES_B = ["Systems", "Labs", "Group", "Technologies", "Holdings", "Logistics",
           "Retail", "Health", "Capital", "Media", "Foods", "Energy", "Robotics",
           "Analytics", "Networks", "Mobility", "Studios", "Ventures"]
PEOPLE = ["A. Romero", "J. Park", "M. Silva", "L. Okafor", "T. Nguyen", "S. Cohen",
          "D. Fischer", "P. Alvarez", "R. Brooks", "K. Sato", "N. Costa", "E. Moreau",
          "C. Bianchi", "G. Haddad", "V. Petrov", "F. Dubois"]


# --------------------------------------------------------------------------
# 1) customer_master
# --------------------------------------------------------------------------
def gen_customers(n=110):
    rows = []
    for i in range(1, n + 1):
        region, country, ccy, entity, tax = wchoice([(g[:5], g[5]) for g in GEO])
        # region/country/ccy/entity/tax are the unpacked geo fields used below
        seg = wchoice(SEGMENTS)
        status = wchoice([("active", 82), ("churned", 9), ("suspended", 4), ("credit-hold", 5)])
        terms = wchoice([("NET30", 50), ("NET45", 20), ("NET15", 15), ("NET60", 12), ("NET90", 3)])
        lo, hi = ARR_BAND[seg]
        limit_usd = round(hi * rng.uniform(0.8, 2.0), -3)
        credit_status = "hold" if status == "credit-hold" else wchoice([("ok", 78), ("watch", 22)])
        risk = wchoice([("low", 55), ("medium", 30), ("high", 15)]) if seg in ("SMB", "Mid-Market") \
            else wchoice([("low", 70), ("medium", 22), ("high", 8)])
        po_req = 1 if (seg in ("Enterprise", "Strategic") and rng.random() < 0.55) \
            or rng.random() < 0.12 else 0
        created = date(2023, 1, 1)
        created = add_months(created, rng.randint(0, 30))
        # most reviews are recent; a few are deliberately stale (> 365 days)
        if i % 17 == 0:
            last_review = date(2024, rng.randint(1, 12), 15)      # stale
        else:
            last_review = add_months(AS_OF, -rng.randint(0, 11))
        name = f"{NAMES_A[(i * 3) % len(NAMES_A)]} {NAMES_B[(i * 5) % len(NAMES_B)]}"
        rows.append({
            "customer_id": f"CUST-{i:04d}", "customer_name": name, "parent_customer_id": "",
            "region": region, "country": country, "legal_entity": entity,
            "customer_segment": seg, "customer_status": status, "default_currency": ccy,
            "payment_terms": terms, "credit_limit": to_local(limit_usd, ccy),
            "credit_status": credit_status, "tax_profile": tax, "po_required_flag": po_req,
            "sales_owner": PEOPLE[(i) % len(PEOPLE)], "revops_owner": PEOPLE[(i + 5) % len(PEOPLE)],
            "collections_owner": PEOPLE[(i + 9) % len(PEOPLE)], "risk_tier": risk,
            "created_date": iso(created), "last_review_date": iso(last_review),
            "_ccy": ccy, "_entity": entity, "_tax": tax,
            "_seg": seg, "_status": status, "_terms": terms, "_po": po_req,
            "_limit_usd": limit_usd, "_risk": risk,
        })
    # a few parent/child relationships among strategic accounts
    strat = [r for r in rows if r["_seg"] == "Strategic"]
    for child in strat[1::2][:6]:
        child["parent_customer_id"] = strat[0]["customer_id"]
    return rows


# --------------------------------------------------------------------------
# 2) crm_opportunities
# --------------------------------------------------------------------------
def gen_opportunities(customers):
    rows = []
    oid = 0
    for c in customers:
        n_opps = wchoice([(3, 22), (4, 30), (5, 26), (6, 15), (7, 7)])
        for _ in range(n_opps):
            oid += 1
            stage = wchoice([("Closed Won", 55), ("Closed Lost", 20), ("Negotiation", 10),
                             ("Proposal", 8), ("Discovery", 7)])
            won = 1 if stage == "Closed Won" else 0
            prod, model, _gl = wchoice([(p, 30 if p[1] == "subscription" else
                                         (22 if p[1] == "usage" else 24)) for p in PRODUCTS])
            freq = wchoice(FREQS) if model in ("subscription", "usage") else "one-time"
            lo, hi = ARR_BAND[c["_seg"]]
            arr_usd = round(rng.uniform(lo, hi), -2)
            years = wchoice([(1, 30), (2, 45), (3, 25)])
            amount_usd = arr_usd * (years if model == "subscription" else 1)
            if model in ("services", "implementation"):
                amount_usd = round(arr_usd * rng.uniform(0.3, 0.8), -2)
                arr_usd = 0.0
            prob = {"Closed Won": 100, "Closed Lost": 0, "Negotiation": 70,
                    "Proposal": 40, "Discovery": 15}[stage]
            # start within the last ~16 months (incl. the current month) so most
            # contracts are still active and some book in the reporting period
            cstart = add_months(AS_OF, -rng.randint(0, 15))
            cend = add_months(cstart, 12 * years - 1)
            close = add_months(cstart, -rng.randint(0, 2)) if won else \
                add_months(AS_OF, -rng.randint(0, 6))
            rows.append({
                "opportunity_id": f"OPP-{oid:05d}", "customer_id": c["customer_id"],
                "opportunity_name": f"{c['customer_name']} - {prod}", "stage": stage,
                "close_date": iso(close) if won else (iso(close) if stage == "Closed Lost" else ""),
                "expected_close_date": iso(close), "amount": to_local(amount_usd, c["_ccy"]),
                "arr_amount": to_local(arr_usd, c["_ccy"]), "currency": c["_ccy"],
                "product_line": prod, "sales_owner": c["sales_owner"], "probability": prob,
                "legal_entity": c["_entity"], "billing_model": model, "billing_frequency": freq,
                "payment_terms": c["_terms"], "contract_start_date": iso(cstart),
                "contract_end_date": iso(cend), "closed_won_flag": won,
                "source_system": "Salesforce", "last_updated_at": iso(add_months(AS_OF, -rng.randint(0, 3))),
                "_cust": c, "_won": won, "_model": model, "_freq": freq, "_prod": prod,
                "_amount_usd": amount_usd, "_arr_usd": arr_usd, "_cstart": cstart, "_cend": cend,
                "_years": years,
            })
    return rows


# --------------------------------------------------------------------------
# 3) contracts  (closed-won opps -> contracts, minus seeded uncontracted)
# --------------------------------------------------------------------------
def gen_contracts(opps):
    won = [o for o in opps if o["_won"] == 1]
    # EXCEPTION A: closed-won opportunities NOT converted to a contract
    uncontracted = set(o["opportunity_id"] for o in won[::13][:8])
    SEEDED["A_closed_won_not_contracted"] = len(uncontracted)
    rows, cid = [], 0
    for o in won:
        if o["opportunity_id"] in uncontracted:
            continue
        cid += 1
        c = o["_cust"]
        poc = 1 if o["_model"] in ("subscription", "usage") else wchoice([(1, 50), (2, 35), (3, 15)])
        non_std = 1 if rng.random() < 0.12 else 0
        rec_method = REC_METHOD[o["_model"]]
        # EXCEPTION (contract data quality): a few non-standard rev rec / missing terms
        status = wchoice([("active", 90), ("expired", 6), ("terminated", 4)])
        if o["_cend"] < AS_OF and status == "active":
            status = "expired"
        rows.append({
            "contract_id": f"CTR-{cid:05d}", "opportunity_id": o["opportunity_id"],
            "customer_id": c["customer_id"], "signed_date": iso(o["_cstart"]),
            "contract_start_date": iso(o["_cstart"]), "contract_end_date": iso(o["_cend"]),
            "contract_value": o["amount"], "arr_amount": o["arr_amount"], "currency": c["_ccy"],
            "legal_entity": c["_entity"], "billing_model": o["_model"],
            "billing_frequency": o["_freq"], "revenue_recognition_method": rec_method,
            "performance_obligation_count": poc, "payment_terms": c["_terms"],
            "auto_renew_flag": 1 if rng.random() < 0.6 else 0, "po_required_flag": c["_po"],
            "non_standard_terms_flag": non_std, "contract_status": status, "source_system": "CLM",
            "_opp": o, "_cust": c, "_status": status, "_recm": rec_method,
        })
    return rows


# --------------------------------------------------------------------------
# 4) sales_orders  (active contracts -> orders, minus seeded unordered)
# --------------------------------------------------------------------------
def gen_orders(contracts):
    active = [c for c in contracts if c["_status"] == "active"]
    # EXCEPTION B: active contracts NOT converted to a sales order
    unordered = set(c["contract_id"] for c in active[::11][:10])
    SEEDED["B_contract_not_ordered"] = len(unordered)
    # EXCEPTION N: customers on credit hold that still get a NEW active order
    hold_custs = [c for c in contracts if c["_cust"]["_status"] == "credit-hold"]
    credit_hold_orders = set()
    rows, oid = [], 0
    for c in contracts:
        if c["_status"] != "active" or c["contract_id"] in unordered:
            continue
        oid += 1
        o = c["_opp"]
        cust = c["_cust"]
        block = 1 if rng.random() < 0.06 else 0
        block_reason = wchoice([("Pending PO", 40), ("Tax review", 30), ("Credit review", 30)]) if block else ""
        rows.append({
            "order_id": f"SO-{oid:05d}", "contract_id": c["contract_id"],
            "opportunity_id": c["opportunity_id"], "customer_id": cust["customer_id"],
            "order_date": iso(o["_cstart"]), "service_start_date": iso(o["_cstart"]),
            "service_end_date": iso(o["_cend"]), "order_amount": c["contract_value"],
            "currency": cust["_ccy"], "legal_entity": cust["_entity"], "product_line": o["_prod"],
            "order_status": wchoice([("active", 80), ("fulfilled", 16), ("cancelled", 4)]),
            "billing_block_flag": block, "billing_block_reason": block_reason,
            "tax_code": cust["_tax"], "po_number": f"PO-{rng.randint(10000, 99999)}" if cust["_po"] else "",
            "source_system": "ERP",
            "_ctr": c, "_cust": cust, "_block": block,
        })
    # seed credit-hold violations: force a handful of hold customers to have an active order
    for c in hold_custs[:6]:
        oid += 1
        cust = c["_cust"]
        o = c["_opp"]
        credit_hold_orders.add(f"SO-{oid:05d}")
        rows.append({
            "order_id": f"SO-{oid:05d}", "contract_id": c["contract_id"],
            "opportunity_id": c["opportunity_id"], "customer_id": cust["customer_id"],
            "order_date": iso(add_months(AS_OF, -1)), "service_start_date": iso(add_months(AS_OF, -1)),
            "service_end_date": iso(o["_cend"]), "order_amount": c["contract_value"],
            "currency": cust["_ccy"], "legal_entity": cust["_entity"], "product_line": o["_prod"],
            "order_status": "active", "billing_block_flag": 0, "billing_block_reason": "",
            "tax_code": cust["_tax"], "po_number": "", "source_system": "ERP",
            "_ctr": c, "_cust": cust, "_block": 0,
        })
    SEEDED["N_credit_hold_new_order"] = len(credit_hold_orders)
    return rows


# --------------------------------------------------------------------------
# 5) billing_schedule  (orders -> scheduled bill lines, minus seeded gaps)
# --------------------------------------------------------------------------
def gen_billing(orders):
    # EXCEPTION C: active sales orders with NO billing schedule at all
    no_sched = set(o["order_id"] for o in orders if o["order_status"] == "active")
    no_sched = set(list(no_sched)[::14][:9])
    SEEDED["C_order_missing_billing_schedule"] = len(no_sched)
    rows, bid = [], 0
    for o in orders:
        if o["order_status"] == "cancelled" or o["order_id"] in no_sched:
            continue
        ctr = o["_ctr"]
        cust = o["_cust"]
        model = ctr["billing_model"]
        freq = ctr["billing_frequency"]
        step = FREQ_MONTHS.get(freq, 12)
        start = date.fromisoformat(o["service_start_date"])
        end = date.fromisoformat(o["service_end_date"])
        horizon = add_months(AS_OF, 3)
        arr_local = ctr["arr_amount"] or 0.0
        if model in ("services", "implementation"):
            # one-to-three milestone / one-time bills near order date
            k = 1 if model == "implementation" else wchoice([(1, 40), (2, 35), (3, 25)])
            per = round(o["order_amount"] / k, 2)
            for j in range(k):
                bid += 1
                bdate = add_months(start, j)
                rows.append(_bline(bid, o, cust, bdate, bdate, add_months(bdate, 1),
                                   per, "billable"))
        else:
            d = start
            while d <= min(end, horizon):
                bid += 1
                if model == "usage":
                    amt = round((arr_local / 12.0) * step * rng.uniform(0.7, 1.3), 2)
                else:
                    amt = round(arr_local * (step / 12.0), 2)
                rows.append(_bline(bid, o, cust, d, d, add_months(d, step), amt, "billable"))
                d = add_months(d, step)
    return rows


def _bline(bid, o, cust, sched_date, svc_start, svc_end, amount, status):
    return {
        "billing_schedule_id": f"BSCH-{bid:06d}", "contract_id": o["contract_id"],
        "order_id": o["order_id"], "customer_id": cust["customer_id"],
        "scheduled_invoice_date": iso(sched_date), "service_period_start": iso(svc_start),
        "service_period_end": iso(svc_end), "scheduled_bill_amount": amount,
        "currency": cust["_ccy"], "billing_status": status, "invoice_id": "",
        "billing_exception_reason": "", "created_at": iso(sched_date),
        "_o": o, "_cust": cust, "_sched": sched_date, "_svc_start": svc_start,
        "_svc_end": svc_end, "_amount": amount,
    }


# --------------------------------------------------------------------------
# 6/7) invoices + credit_memos  (billable lines due by horizon -> invoices)
# --------------------------------------------------------------------------
def gen_invoices(billing):
    # only bill lines scheduled on/before as-of are due to be invoiced now
    due = [b for b in billing if b["_sched"] <= AS_OF]
    # EXCEPTION D: billable, due lines that were NOT invoiced (revenue leakage)
    not_invoiced = set(b["billing_schedule_id"] for b in due[::9][:12])
    SEEDED["D_billable_not_invoiced"] = len(not_invoiced)
    # blocked-billing lines (valid reason) - not a leakage exception
    for b in due[::23][:10]:
        if b["billing_schedule_id"] not in not_invoiced:
            b["billing_status"] = "blocked"
            b["billing_exception_reason"] = wchoice(
                [("Customer dispute", 40), ("Pending PO", 35), ("Hold for credit review", 25)])

    invoices, memos = [], []
    iid = mid = 0
    # pick seed sets up front for amount mismatch / late / dup / tax / fx
    inv_candidates = [b for b in due if b["billing_schedule_id"] not in not_invoiced
                      and b["billing_status"] != "blocked"]
    mismatch = set(b["billing_schedule_id"] for b in inv_candidates[::17][:14])
    late = set(b["billing_schedule_id"] for b in inv_candidates[5::13][:30])
    bad_tax = set(b["billing_schedule_id"] for b in inv_candidates[3::19][:9])
    wrong_ccy = set(b["billing_schedule_id"] for b in inv_candidates[7::29][:5])
    dup_src = inv_candidates[2::37][:6]
    SEEDED["E_invoice_amount_mismatch"] = len(mismatch)
    SEEDED["late_invoices"] = len(late)
    SEEDED["invalid_tax_treatment"] = len(bad_tax)
    SEEDED["wrong_currency"] = len(wrong_ccy)
    SEEDED["G_duplicate_invoices"] = len(dup_src)
    SEEDED["F_missing_po_required"] = 0

    def make_invoice(b, force_dup=False):
        nonlocal iid
        iid += 1
        cust = b["_cust"]
        o = b["_o"]
        ctr = o["_ctr"]
        bid = b["billing_schedule_id"]
        amt = b["_amount"]
        if bid in mismatch:                       # EXCEPTION E
            amt = round(amt * rng.choice([1.15, 0.85, 1.25]), 2)
        inv_date = b["_sched"]
        if bid in late:                           # late vs scheduled (timeliness)
            inv_date = add_months(b["_sched"], 0)
            inv_date = date.fromordinal(inv_date.toordinal() + rng.randint(7, 25))
        terms = cust["_terms"]
        due_date = date.fromordinal(inv_date.toordinal() + terms_days(terms))
        tax_profile = cust["_tax"]
        rate = TAX_RATE.get(tax_profile, 0.0)
        tax = round(amt * rate, 2)
        if bid in bad_tax:                        # EXCEPTION: invalid tax treatment
            tax = 0.0 if rate > 0 else round(amt * 0.15, 2)
        ccy = cust["_ccy"]
        if bid in wrong_ccy:                      # EXCEPTION: wrong currency
            ccy = "USD" if ccy != "USD" else "EUR"
        po = o["po_number"]
        # EXCEPTION F: PO required by customer but missing on invoice
        if cust["_po"] and rng.random() < 0.20:
            po = ""
            SEEDED["F_missing_po_required"] += 1
        inv_id = f"INV-{iid:06d}"
        b["invoice_id"] = inv_id
        b["billing_status"] = "billed"
        return {
            "invoice_id": inv_id, "order_id": o["order_id"], "contract_id": o["contract_id"],
            "customer_id": cust["customer_id"], "invoice_date": iso(inv_date),
            "due_date": iso(due_date), "invoice_amount": amt, "tax_amount": tax,
            "total_invoice_amount": round(amt + tax, 2), "currency": ccy,
            "legal_entity": cust["_entity"], "invoice_status": "open",
            "payment_terms": terms, "po_number": po,
            "service_period_start": b["service_period_start"],
            "service_period_end": b["service_period_end"], "gl_ar_account": "1200-AR",
            "gl_revenue_account": _gl_for(ctr["billing_model"]), "source_system": "ERP",
            "_b": b, "_cust": cust, "_ctr": ctr, "_inv_date": inv_date, "_due": due_date,
            "_amt": amt, "_tax": tax, "_total": round(amt + tax, 2), "_ccy": ccy,
        }

    for b in inv_candidates:
        invoices.append(make_invoice(b))
    # EXCEPTION G: duplicate invoices (same customer/order/period/amount)
    for b in dup_src:
        invoices.append(make_invoice(b, force_dup=True))

    # credit memos against a sample of invoices
    memo_targets = invoices[::9][:90]
    SEEDED["credit_memos"] = len(memo_targets)
    for inv in memo_targets:
        mid += 1
        cust = inv["_cust"]
        amt = round(inv["_amt"] * rng.uniform(0.1, 0.4), 2)
        memos.append({
            "credit_memo_id": f"CM-{mid:05d}", "invoice_id": inv["invoice_id"],
            "customer_id": cust["customer_id"], "credit_date": iso(add_months(inv["_inv_date"], 1)),
            "credit_amount": amt, "currency": inv["_ccy"],
            "reason_code": wchoice([("Price adjustment", 30), ("Service credit", 30),
                                    ("Billing error", 25), ("Goodwill", 15)]),
            "approved_by": rng.choice(PEOPLE),
            "approval_status": wchoice([("approved", 82), ("pending", 18)]),
            "gl_account": "4900-Contra-Rev", "created_at": iso(add_months(inv["_inv_date"], 1)),
            "_amt": amt, "_inv": inv,
        })
    return invoices, memos


def _gl_for(model):
    return {"subscription": "4000-Subscription-Rev", "usage": "4100-Usage-Rev",
            "services": "4200-Services-Rev", "implementation": "4300-Implementation-Rev"}[model]


# --------------------------------------------------------------------------
# 8/9/10) payments + bank_receipts + cash_application
# --------------------------------------------------------------------------
def gen_cash(invoices, memos):
    payments, receipts, applications = [], [], []
    pid = rid = aid = 0
    memo_by_inv = {}
    for m in memos:
        if m["approval_status"] == "approved":
            memo_by_inv[m["invoice_id"]] = memo_by_inv.get(m["invoice_id"], 0.0) + m["_amt"]

    # decide which invoices get paid (older + lower risk -> more likely paid)
    pay_plan = []
    for inv in invoices:
        cust = inv["_cust"]
        due = inv["_due"]
        days_overdue = (AS_OF - due).days
        risk = {"low": 0.0, "medium": 0.08, "high": 0.18}[cust["_risk"]]
        if due > AS_OF:
            p_paid = 0.10                      # not due yet: mostly open
        else:
            # collect most overdue, but leave a believable working AR book
            p_paid = min(0.86, 0.50 + days_overdue / 260.0) - risk
        pay_plan.append((inv, max(0.03, p_paid)))

    paid = [inv for inv, p in pay_plan if rng.random() < p]
    # seed payment-quality exceptions among the paid set
    short = set(id(inv) for inv in paid[::11][:13])
    over = set(id(inv) for inv in paid[5::13][:9])
    no_bank = set(id(inv) for inv in paid[3::23][:8])     # payment exists, no bank receipt
    SEEDED["short_payments"] = len(short)
    SEEDED["overpayments"] = len(over)
    SEEDED["payment_not_in_bank"] = len(no_bank)

    for inv in paid:
        pid += 1
        cust = inv["_cust"]
        net_due = round(inv["_total"] - memo_by_inv.get(inv["invoice_id"], 0.0), 2)
        amt = net_due
        if id(inv) in short:
            amt = round(net_due * rng.uniform(0.6, 0.9), 2)
        elif id(inv) in over:
            amt = round(net_due * rng.uniform(1.05, 1.2), 2)
        pay_date = date.fromordinal(min(AS_OF.toordinal(),
                                        inv["_due"].toordinal() + rng.randint(-5, 40)))
        has_bank = id(inv) not in no_bank
        br_id = ""
        if has_bank:
            rid += 1
            br_id = f"BR-{rid:06d}"
            receipts.append({
                "bank_receipt_id": br_id, "bank_account_id": f"BANK-{cust['_entity'][:3].upper()}-01",
                "bank_date": iso(pay_date), "receipt_amount": amt, "currency": inv["_ccy"],
                "fx_rate_to_usd": FX.get(inv["_ccy"], 1.0), "legal_entity": cust["_entity"],
                "bank_reference": f"WIRE{rng.randint(100000, 999999)}",
                "source_bank_file": f"bank_{month_str(pay_date)}.csv", "matched_status": "matched",
                "created_at": iso(pay_date), "_amt": amt, "_inv": inv,
            })
        payments.append({
            "payment_id": f"PMT-{pid:06d}", "customer_id": cust["customer_id"],
            "payment_date": iso(pay_date), "payment_amount": amt, "currency": inv["_ccy"],
            "payment_method": wchoice([("Wire", 45), ("ACH", 35), ("Card", 12), ("Check", 8)]),
            "bank_receipt_id": br_id, "remittance_reference": inv["invoice_id"],
            "payer_name": cust["customer_name"], "payment_status": "received",
            "created_at": iso(pay_date), "_inv": inv, "_amt": amt, "_br": br_id, "_has_bank": has_bank,
        })

    # cash application: apply matched receipts to their invoice; seed unapplied.
    # Use lists (not sets of ids) so the selection is deterministic across runs.
    apply_plan = [p for p in payments if p["_has_bank"]]
    unapplied_list = apply_plan[4::15][:14]                   # EXCEPTION: unapplied cash
    unapplied = set(id(p) for p in unapplied_list)
    no_reason = set(id(p) for p in unapplied_list[::3][:5])   # unapplied AND undocumented
    SEEDED["unapplied_cash"] = len(unapplied)
    SEEDED["J_unapplied_no_reason"] = len(no_reason)
    for p in payments:
        aid += 1
        inv = p["_inv"]
        if not p["_has_bank"]:
            continue
        status = "unapplied" if id(p) in unapplied else "applied"
        applied_amt = 0.0 if status == "unapplied" else p["_amt"]
        discount = round(p["_amt"] * 0.01, 2) if rng.random() < 0.05 else 0.0
        writeoff = 0.0
        if status == "unapplied":
            reason = "" if id(p) in no_reason else "Remittance not identified"
        else:
            reason = ""
        # small FX gain/loss when a local-currency receipt is revalued
        fxgl = 0.0
        if inv["_ccy"] != "USD" and status == "applied" and rng.random() < 0.25:
            fxgl = round(p["_amt"] * FX[inv["_ccy"]] * rng.uniform(-0.02, 0.02), 2)
        applications.append({
            "cash_application_id": f"CA-{aid:06d}", "payment_id": p["payment_id"],
            "bank_receipt_id": p["_br"], "invoice_id": inv["invoice_id"],
            "customer_id": p["customer_id"], "applied_date": p["payment_date"],
            "applied_amount": applied_amt, "discount_taken": discount, "writeoff_amount": writeoff,
            "fx_gain_loss": fxgl, "application_status": status,
            "unapplied_reason": reason,
            "created_at": p["payment_date"], "_inv": inv, "_applied": applied_amt,
        })

    # A bank receipt is 'matched' only if its cash was actually applied to an
    # invoice. Receipts behind an unapplied application are 'unmatched' (cash in
    # the bank, not yet applied to AR) - this is the cash-application exception.
    applied_receipts = set(a["bank_receipt_id"] for a in applications
                           if a["application_status"] == "applied" and a["bank_receipt_id"])
    unmatched = 0
    for r in receipts:
        if r["bank_receipt_id"] in applied_receipts:
            r["matched_status"] = "matched"
        else:
            r["matched_status"] = "unmatched"
            unmatched += 1
    SEEDED["bank_receipt_unapplied"] = unmatched
    return payments, receipts, applications


# --------------------------------------------------------------------------
# 11/12) revenue_schedule + deferred_revenue_rollforward
# --------------------------------------------------------------------------
def gen_revenue(invoices):
    rows, rid = [], 0
    # EXCEPTION seeds
    before_start = set()
    after_end = set()
    cand = [i for i in invoices if i["_ctr"]["billing_model"] in ("subscription",)]
    bs_pick = cand[::21][:8]
    ae_pick = cand[7::23][:7]
    for inv in invoices:
        rid_local = []
        cust = inv["_cust"]
        ctr = inv["_ctr"]
        model = ctr["billing_model"]
        svc_start = date.fromisoformat(inv["service_period_start"])
        svc_end = date.fromisoformat(inv["service_period_end"])
        amt = inv["_amt"]
        months = max(1, (svc_end.year - svc_start.year) * 12 + (svc_end.month - svc_start.month))
        if model in ("usage", "implementation", "services"):
            months = 1
        per = round(amt / months, 2)
        deferred = amt
        for k in range(months):
            rid += 1
            rmonth = add_months(svc_start, k)
            deferred = round(deferred - per, 2)
            recognized = per
            status = "recognized" if rmonth <= AS_OF else "scheduled"
            rows.append(_revrow(rid, inv, cust, ctr, rmonth,
                                 recognized, max(0.0, deferred), status))
        # EXCEPTION: revenue recognized BEFORE service start
        if inv in bs_pick:
            rid += 1
            before_start.add(inv["invoice_id"])
            rows.append(_revrow(rid, inv, cust, ctr, add_months(svc_start, -1),
                                 per, 0.0, "recognized"))
        # EXCEPTION: revenue recognized AFTER contract end without renewal
        if inv in ae_pick and ctr["auto_renew_flag"] == 0:
            rid += 1
            after_end.add(inv["invoice_id"])
            rows.append(_revrow(rid, inv, cust, ctr, add_months(svc_end, 2),
                                 per, 0.0, "recognized"))
    SEEDED["K_revenue_before_service_start"] = len(before_start)
    SEEDED["revenue_after_contract_end"] = len(after_end)

    deferred_rows = gen_deferred(rows, invoices)
    return rows, deferred_rows


def _revrow(rid, inv, cust, ctr, rmonth, recognized, deferred, status):
    return {
        "revenue_schedule_id": f"REV-{rid:06d}", "contract_id": ctr["contract_id"],
        "invoice_id": inv["invoice_id"], "customer_id": cust["customer_id"],
        "revenue_month": month_str(rmonth), "performance_obligation": ctr["billing_model"],
        "recognition_method": ctr["_recm"], "recognized_revenue": recognized,
        "deferred_revenue_amount": deferred, "currency": inv["_ccy"],
        "legal_entity": cust["_entity"], "gl_revenue_account": inv["gl_revenue_account"],
        "recognition_status": status,
    }


def gen_deferred(rev_rows, invoices):
    """Monthly deferred-revenue rollforward by (contract, entity, currency).

    Built so the clean rows FOOT exactly: closing = opening + billings
    - recognized + adjustments + fx_impact. A few rows are seeded broken.
    """
    bill_by = {}     # (contract, period) -> billings (invoice amount in month billed)
    for inv in invoices:
        key = (inv["contract_id"], month_str(inv["_inv_date"]))
        bill_by[key] = bill_by.get(key, 0.0) + inv["_amt"]
    rec_by = {}      # (contract, period) -> recognized
    meta = {}
    for r in rev_rows:
        if r["recognition_status"] != "recognized":
            continue
        key = (r["contract_id"], r["revenue_month"])
        rec_by[key] = rec_by.get(key, 0.0) + r["recognized_revenue"]
        meta[r["contract_id"]] = (r["customer_id"], r["legal_entity"], r["currency"])

    contracts = sorted(set([k[0] for k in bill_by] + [k[0] for k in rec_by]))
    periods = [month_str(add_months(date(2025, 1, 1), i)) for i in range(17)]  # 2025-01..2026-05
    rows = []
    seeded_break = 0
    broken_keys = set()
    flat = []
    for ctr in contracts:
        opening = 0.0
        for per in periods:
            billings = round(bill_by.get((ctr, per), 0.0), 2)
            recognized = round(rec_by.get((ctr, per), 0.0), 2)
            adjustments = 0.0
            fx_impact = 0.0
            closing = round(opening + billings - recognized + adjustments + fx_impact, 2)
            if billings == 0 and recognized == 0 and opening == 0:
                opening = closing
                continue
            cust, entity, ccy = meta.get(ctr, ("", "", "USD"))
            flat.append({
                "period": per, "customer_id": cust, "contract_id": ctr, "legal_entity": entity,
                "currency": ccy, "opening_deferred_revenue": opening, "billings": billings,
                "recognized_revenue": recognized, "adjustments": adjustments,
                "fx_impact": fx_impact, "closing_deferred_revenue": closing,
            })
            opening = closing
    # EXCEPTION L: break the rollforward math on a few rows (closing not footing)
    break_pick = flat[9::40][:6]
    for row in break_pick:
        row["closing_deferred_revenue"] = round(row["closing_deferred_revenue"]
                                                + rng.choice([1500.0, -2200.0, 3100.0]), 2)
        seeded_break += 1
    SEEDED["L_deferred_rollforward_break"] = seeded_break
    return flat


# --------------------------------------------------------------------------
# 13/14) collections_activity + disputes
# --------------------------------------------------------------------------
def gen_collections_disputes(invoices, memos, applications):
    # compute open per invoice to target overdue ones
    applied = {}
    for a in applications:
        applied[a["invoice_id"]] = applied.get(a["invoice_id"], 0.0) + a["_applied"]
    credit = {}
    for m in memos:
        if m["approval_status"] == "approved":
            credit[m["invoice_id"]] = credit.get(m["invoice_id"], 0.0) + m["_amt"]

    open_invs = []
    for inv in invoices:
        openamt = round(inv["_total"] - applied.get(inv["invoice_id"], 0.0)
                        - credit.get(inv["invoice_id"], 0.0), 2)
        # invoice_status reflects settlement, derived from the transactions
        inv["invoice_status"] = "open" if openamt > 1.0 else "paid"
        if openamt > 1.0:
            inv["_open"] = openamt
            open_invs.append(inv)
    # EXCEPTION H: subledger status out of sync with the transactions (status says
    # 'paid' but the invoice still carries an open balance) -> AR subledger does
    # not tie to the transaction-derived control balance.
    h_break = open_invs[9::40][:6]
    for inv in h_break:
        inv["invoice_status"] = "paid"
    SEEDED["H_ar_subledger_break"] = len(h_break)
    overdue = [i for i in open_invs if i["_due"] < AS_OF]

    # disputes on a sample of the larger overdue invoices; the rest of overdue AR
    # is normal collections workload (disputes are routed out of collections).
    disputes, did = [], 0
    # sample disputes ACROSS the size distribution (not just the largest invoices)
    # so disputed AR is a believable slice of the book, not a tail-heavy outlier.
    overdue_sorted = sorted(overdue, key=lambda i: i["_open"])
    step = max(1, len(overdue_sorted) // 130)
    disp_targets = overdue_sorted[5::step][:110]
    blocked = 0
    disputed_invoices = set()
    for inv in disp_targets:
        did += 1
        cust = inv["_cust"]
        blocked_flag = 1 if rng.random() < 0.7 else 0
        blocked += blocked_flag
        disputed_invoices.add(inv["invoice_id"])
        disputes.append({
            "dispute_id": f"DISP-{did:05d}", "invoice_id": inv["invoice_id"],
            "customer_id": cust["customer_id"], "opened_date": iso(add_months(inv["_due"], 0)),
            "disputed_amount": round(inv["_open"] * rng.uniform(0.4, 1.0), 2), "currency": inv["_ccy"],
            "reason_code": wchoice([("Pricing", 28), ("Service quality", 24), ("Billing error", 26),
                                    ("Contract terms", 12), ("Duplicate", 10)]),
            "owner_team": wchoice([("Sales", 30), ("Billing", 30), ("Customer Success", 25),
                                   ("Legal", 15)]),
            "root_cause": wchoice([("Quote-to-cash gap", 35), ("Manual error", 30),
                                   ("Expectation gap", 35)]),
            "dispute_status": wchoice([("open", 64), ("resolved", 28), ("escalated", 8)]),
            "expected_resolution_date": iso(add_months(AS_OF, 1)),
            "cash_blocked_flag": blocked_flag, "created_at": iso(add_months(inv["_due"], 0)),
        })
    SEEDED["disputes"] = did
    SEEDED["disputes_cash_blocked"] = blocked

    # collections activity on ALL overdue invoices (a collector still logs an
    # activity on a disputed invoice before routing it out); the cash FORECAST,
    # not the activity log, is what excludes disputed cash.
    acts, cid = [], 0
    broken_promise = 0
    coll_targets = list(overdue)
    for inv in coll_targets[::1]:
        n = wchoice([(1, 30), (2, 40), (3, 30)])
        cust = inv["_cust"]
        for j in range(n):
            cid += 1
            atype = wchoice([("Call", 30), ("Email", 35), ("Dunning notice", 25), ("Escalation", 10)])
            promise = ""
            promised_amt = ""
            outcome = wchoice([("No response", 30), ("Promise to pay", 30),
                               ("Paid", 15), ("Dispute raised", 10), ("Left message", 15)])
            esc = wchoice([(0, 60), (1, 25), (2, 10), (3, 5)])
            if outcome == "Promise to pay":
                # some promises are already broken (date passed, still open)
                if rng.random() < 0.45:
                    pdate = date.fromordinal(AS_OF.toordinal() - rng.randint(3, 30))
                    broken_promise += 1
                else:
                    pdate = date.fromordinal(AS_OF.toordinal() + rng.randint(3, 25))
                promise = iso(pdate)
                promised_amt = round(inv["_open"] * rng.uniform(0.5, 1.0), 2)
            acts.append({
                "collections_activity_id": f"COL-{cid:06d}", "invoice_id": inv["invoice_id"],
                "customer_id": cust["customer_id"],
                "activity_date": iso(date.fromordinal(AS_OF.toordinal() - rng.randint(1, 45))),
                "activity_type": atype, "owner": cust["collections_owner"],
                "promise_to_pay_date": promise, "promised_amount": promised_amt,
                "outcome": outcome, "next_step": wchoice([("Follow up", 50), ("Escalate", 25),
                                                          ("Send statement", 25)]),
                "escalation_level": esc, "created_at": iso(AS_OF),
            })
    SEEDED["collections_activities"] = cid
    SEEDED["broken_promises"] = broken_promise
    return acts, disputes, open_invs


# --------------------------------------------------------------------------
# 15) credit_limits  (one active policy per customer + historical reviews)
# --------------------------------------------------------------------------
def gen_credit_limits(customers, open_invs):
    exposure = {}
    for inv in open_invs:
        c = inv["customer_id"]
        exposure[c] = exposure.get(c, 0.0) + inv["_open"]
    rows, pid = [], 0
    # EXCEPTION M: exposure ABOVE limit without approval (breach)
    breach_custs = set(c["customer_id"] for c in customers[::11][:10])
    SEEDED["M_credit_limit_breach"] = len(breach_custs)
    for c in customers:
        pid += 1
        cid = c["customer_id"]
        limit_local = c["credit_limit"]
        exp_local = round(exposure.get(cid, 0.0), 2)
        if cid in breach_custs:
            exp_local = round(limit_local * rng.uniform(1.1, 1.6), 2)   # force breach
        util = round((exp_local / limit_local * 100.0) if limit_local else 0.0, 1)
        hold = 1 if c["_status"] == "credit-hold" else 0
        risk_score = {"low": rng.randint(70, 95), "medium": rng.randint(45, 70),
                      "high": rng.randint(15, 45)}[c["_risk"]]
        rows.append({
            "credit_policy_id": f"CRP-{pid:05d}", "customer_id": cid,
            "effective_date": c["last_review_date"], "credit_limit": limit_local,
            "currency": c["_ccy"], "current_exposure_amount": exp_local, "utilization_pct": util,
            "credit_status": c["credit_status"], "hold_flag": hold,
            "approved_by": rng.choice(PEOPLE),
            "next_review_date": iso(add_months(date.fromisoformat(c["last_review_date"]), 12)),
            "risk_score": risk_score,
        })
        # one historical review row for ~ a third of customers
        if pid % 3 == 0:
            pid += 1
            rows.append({
                "credit_policy_id": f"CRP-{pid:05d}", "customer_id": cid,
                "effective_date": iso(add_months(date.fromisoformat(c["last_review_date"]), -12)),
                "credit_limit": round(limit_local * 0.8, 2), "currency": c["_ccy"],
                "current_exposure_amount": round(exp_local * 0.7, 2),
                "utilization_pct": round(util * 0.7, 1), "credit_status": "ok", "hold_flag": 0,
                "approved_by": rng.choice(PEOPLE),
                "next_review_date": c["last_review_date"], "risk_score": risk_score,
            })
    return rows


# --------------------------------------------------------------------------
# Column orders (the schemas the loader validates against)
# --------------------------------------------------------------------------
COLS = {
    "customer_master.csv": ["customer_id", "customer_name", "parent_customer_id", "region",
        "country", "legal_entity", "customer_segment", "customer_status", "default_currency",
        "payment_terms", "credit_limit", "credit_status", "tax_profile", "po_required_flag",
        "sales_owner", "revops_owner", "collections_owner", "risk_tier", "created_date",
        "last_review_date"],
    "crm_opportunities.csv": ["opportunity_id", "customer_id", "opportunity_name", "stage",
        "close_date", "expected_close_date", "amount", "arr_amount", "currency", "product_line",
        "sales_owner", "probability", "legal_entity", "billing_model", "billing_frequency",
        "payment_terms", "contract_start_date", "contract_end_date", "closed_won_flag",
        "source_system", "last_updated_at"],
    "contracts.csv": ["contract_id", "opportunity_id", "customer_id", "signed_date",
        "contract_start_date", "contract_end_date", "contract_value", "arr_amount", "currency",
        "legal_entity", "billing_model", "billing_frequency", "revenue_recognition_method",
        "performance_obligation_count", "payment_terms", "auto_renew_flag", "po_required_flag",
        "non_standard_terms_flag", "contract_status", "source_system"],
    "sales_orders.csv": ["order_id", "contract_id", "opportunity_id", "customer_id", "order_date",
        "service_start_date", "service_end_date", "order_amount", "currency", "legal_entity",
        "product_line", "order_status", "billing_block_flag", "billing_block_reason", "tax_code",
        "po_number", "source_system"],
    "billing_schedule.csv": ["billing_schedule_id", "contract_id", "order_id", "customer_id",
        "scheduled_invoice_date", "service_period_start", "service_period_end",
        "scheduled_bill_amount", "currency", "billing_status", "invoice_id",
        "billing_exception_reason", "created_at"],
    "invoices.csv": ["invoice_id", "order_id", "contract_id", "customer_id", "invoice_date",
        "due_date", "invoice_amount", "tax_amount", "total_invoice_amount", "currency",
        "legal_entity", "invoice_status", "payment_terms", "po_number", "service_period_start",
        "service_period_end", "gl_ar_account", "gl_revenue_account", "source_system"],
    "credit_memos.csv": ["credit_memo_id", "invoice_id", "customer_id", "credit_date",
        "credit_amount", "currency", "reason_code", "approved_by", "approval_status", "gl_account",
        "created_at"],
    "payments.csv": ["payment_id", "customer_id", "payment_date", "payment_amount", "currency",
        "payment_method", "bank_receipt_id", "remittance_reference", "payer_name",
        "payment_status", "created_at"],
    "bank_receipts.csv": ["bank_receipt_id", "bank_account_id", "bank_date", "receipt_amount",
        "currency", "fx_rate_to_usd", "legal_entity", "bank_reference", "source_bank_file",
        "matched_status", "created_at"],
    "cash_application.csv": ["cash_application_id", "payment_id", "bank_receipt_id", "invoice_id",
        "customer_id", "applied_date", "applied_amount", "discount_taken", "writeoff_amount",
        "fx_gain_loss", "application_status", "unapplied_reason", "created_at"],
    "revenue_schedule.csv": ["revenue_schedule_id", "contract_id", "invoice_id", "customer_id",
        "revenue_month", "performance_obligation", "recognition_method", "recognized_revenue",
        "deferred_revenue_amount", "currency", "legal_entity", "gl_revenue_account",
        "recognition_status"],
    "deferred_revenue_rollforward.csv": ["period", "customer_id", "contract_id", "legal_entity",
        "currency", "opening_deferred_revenue", "billings", "recognized_revenue", "adjustments",
        "fx_impact", "closing_deferred_revenue"],
    "collections_activity.csv": ["collections_activity_id", "invoice_id", "customer_id",
        "activity_date", "activity_type", "owner", "promise_to_pay_date", "promised_amount",
        "outcome", "next_step", "escalation_level", "created_at"],
    "disputes.csv": ["dispute_id", "invoice_id", "customer_id", "opened_date", "disputed_amount",
        "currency", "reason_code", "owner_team", "root_cause", "dispute_status",
        "expected_resolution_date", "cash_blocked_flag", "created_at"],
    "credit_limits.csv": ["credit_policy_id", "customer_id", "effective_date", "credit_limit",
        "currency", "current_exposure_amount", "utilization_pct", "credit_status", "hold_flag",
        "approved_by", "next_review_date", "risk_score"],
}


def main():
    os.makedirs(DATA, exist_ok=True)
    customers = gen_customers(125)
    opps = gen_opportunities(customers)
    contracts = gen_contracts(opps)
    orders = gen_orders(contracts)
    billing = gen_billing(orders)
    invoices, memos = gen_invoices(billing)
    payments, receipts, applications = gen_cash(invoices, memos)
    rev_rows, deferred_rows = gen_revenue(invoices)
    acts, disputes, open_invs = gen_collections_disputes(invoices, memos, applications)
    credit_rows = gen_credit_limits(customers, open_invs)

    counts = {}
    counts["customer_master.csv"] = write_csv("customer_master.csv", customers, COLS["customer_master.csv"])
    counts["crm_opportunities.csv"] = write_csv("crm_opportunities.csv", opps, COLS["crm_opportunities.csv"])
    counts["contracts.csv"] = write_csv("contracts.csv", contracts, COLS["contracts.csv"])
    counts["sales_orders.csv"] = write_csv("sales_orders.csv", orders, COLS["sales_orders.csv"])
    counts["billing_schedule.csv"] = write_csv("billing_schedule.csv", billing, COLS["billing_schedule.csv"])
    counts["invoices.csv"] = write_csv("invoices.csv", invoices, COLS["invoices.csv"])
    counts["credit_memos.csv"] = write_csv("credit_memos.csv", memos, COLS["credit_memos.csv"])
    counts["payments.csv"] = write_csv("payments.csv", payments, COLS["payments.csv"])
    counts["bank_receipts.csv"] = write_csv("bank_receipts.csv", receipts, COLS["bank_receipts.csv"])
    counts["cash_application.csv"] = write_csv("cash_application.csv", applications, COLS["cash_application.csv"])
    counts["revenue_schedule.csv"] = write_csv("revenue_schedule.csv", rev_rows, COLS["revenue_schedule.csv"])
    counts["deferred_revenue_rollforward.csv"] = write_csv(
        "deferred_revenue_rollforward.csv", deferred_rows, COLS["deferred_revenue_rollforward.csv"])
    counts["collections_activity.csv"] = write_csv("collections_activity.csv", acts, COLS["collections_activity.csv"])
    counts["disputes.csv"] = write_csv("disputes.csv", disputes, COLS["disputes.csv"])
    counts["credit_limits.csv"] = write_csv("credit_limits.csv", credit_rows, COLS["credit_limits.csv"])

    print("Generated O2C datasets (rows):")
    for k in COLS:
        print(f"  {k:38} {counts[k]:>6}")
    print("\nSeeded exceptions (ground truth for the controls):")
    for k, v in SEEDED.items():
        print(f"  {k:34} {v:>4}")


if __name__ == "__main__":
    main()
