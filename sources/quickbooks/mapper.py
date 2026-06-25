"""
mapper.py - Pure, deterministic QuickBooks -> canonical transform.

This is where QuickBooks names die. Every QBO account is routed into one of the
12 canonical rollup codes (schema.CANONICAL_COA); every report and entity is
turned into the canonical tables finance_core already reads. No model, no
network, no randomness: same input -> same output, always.

Account routing (QBO AccountType / name -> canonical code), documented:
  Bank                                  -> 1000 cash
  Accounts Receivable                   -> 1100 AR
  Fixed Asset / other asset             -> 1500 fixed/other assets
  Accounts Payable                      -> 2000 AP
  liability named deferred/unearned     -> 2500 deferred revenue
  other liability                       -> 2000 AP bucket
  equity named retained/net income      -> 3900 retained earnings
  other equity                          -> 3000 paid-in capital
  Income / Other Income                 -> 4000 revenue
  Cost of Goods Sold                    -> 5000 cost of revenue
  Expense named marketing/sales/ads     -> 6000 sales & marketing
  Expense named research/development/eng-> 6100 R&D
  other expense                         -> 6200 G&A

Boundary (honest): QBO sandbox is single-entity / single-currency, so entity is a
single id and currency is USD. The 3-bucket asset model (cash / AR / other) and
the S&M/R&D/G&A split are deterministic roll-ups, not QBO's full chart.
"""

from schema import (CANONICAL_COA, COA_NAME, REPORTING_CURRENCY,
                    PNL_REVENUE, PNL_COGS, PNL_SM, PNL_RD, PNL_GA,
                    BS_CASH, BS_AR, BS_FIXED, BS_AP, BS_DEFERRED,
                    BS_PAID_IN, BS_RETAINED, OPEN, PAID)

_SM_WORDS = ("market", "sales", "advertis", "promot", "commission")
_RD_WORDS = ("research", "develop", "r&d", "engineer", "product")
_DEFERRED_WORDS = ("deferred", "unearned")
_RETAINED_WORDS = ("retained", "net income", "accumulated")


def _money(v):
    """Parse a QBO amount (string from reports, number from entities)."""
    if v is None or v == "":
        return 0.0
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _lc(s):
    return (s or "").strip().lower()


# --------------------------------------------------------------------------
# Account routing
# --------------------------------------------------------------------------
def canonical_code_for_account(account):
    """Map one QBO Account object -> canonical rollup code (or None to ignore)."""
    atype = _lc(account.get("AccountType"))
    name = _lc(account.get("Name") or account.get("FullyQualifiedName"))
    if atype == "bank":
        return BS_CASH
    if atype == "accounts receivable":
        return BS_AR
    if atype in ("fixed asset", "other asset", "other current asset"):
        return BS_FIXED
    if atype == "accounts payable":
        return BS_AP
    if atype in ("credit card", "other current liability", "long term liability"):
        return BS_DEFERRED if any(w in name for w in _DEFERRED_WORDS) else BS_AP
    if atype == "equity":
        return BS_RETAINED if any(w in name for w in _RETAINED_WORDS) else BS_PAID_IN
    if atype in ("income", "other income"):
        return PNL_REVENUE
    if atype == "cost of goods sold":
        return PNL_COGS
    if atype in ("expense", "other expense"):
        return _expense_code(name)
    return None


def _expense_code(name):
    if any(w in name for w in _SM_WORDS):
        return PNL_SM
    if any(w in name for w in _RD_WORDS):
        return PNL_RD
    return PNL_GA


def account_code_map(accounts):
    """{account_name -> canonical_code} from the Account query, used to route
    report leaf rows (which are keyed by account name)."""
    out = {}
    for a in accounts or []:
        code = canonical_code_for_account(a)
        if code:
            out[a.get("Name", "")] = code
            fq = a.get("FullyQualifiedName")
            if fq:
                out[fq] = code
    return out


# --------------------------------------------------------------------------
# Report tree walk (ProfitAndLoss / BalanceSheet share the shape)
# --------------------------------------------------------------------------
def walk_report_leaves(report):
    """Yield (group, account_name, amount) for every Data leaf in a QBO report.

    `group` is the nearest ancestor section group (Income, COGS, Expenses,
    Assets, Liabilities, Equity, ...). Summary rows are skipped to avoid
    double-counting.
    """
    rows = (report or {}).get("Rows", {}).get("Row", [])

    def visit(node, group):
        g = node.get("group", group)
        coldata = node.get("ColData")
        nested = node.get("Rows", {}).get("Row")
        if coldata and not nested and node.get("type", "Data") != "Section":
            name = coldata[0].get("value", "")
            amount = _money(coldata[-1].get("value")) if len(coldata) > 1 else 0.0
            if name:
                yield (g, name, amount)
        for child in (nested or []):
            yield from visit(child, g)

    for r in rows:
        yield from visit(r, None)


# --------------------------------------------------------------------------
# Canonical table builders
# --------------------------------------------------------------------------
def map_entities(entity_id, entity_name, country="United States"):
    return [{"entity_id": entity_id, "name": entity_name,
             "country": country, "currency": REPORTING_CURRENCY}]


def map_fx_rates(periods):
    # Single currency source: USD trades 1:1 with itself for every period.
    return [{"period": p, "currency": REPORTING_CURRENCY, "units_per_usd": "1"}
            for p in sorted(set(periods))]


def map_chart_of_accounts():
    # Source-independent canonical roll-up chart.
    return [dict(a) for a in CANONICAL_COA]


def _group_to_pnl_code(group, name):
    g = _lc(group)
    if "income" in g:
        return PNL_REVENUE
    if "cogs" in g or "cost of goods" in g:
        return PNL_COGS
    if "expense" in g:
        return _expense_code(_lc(name))
    return None


def map_pnl(pl_report, code_map, entity_id, period):
    """ProfitAndLoss report -> pnl_activity rows (positive magnitudes per code)."""
    agg = {}
    for group, name, amount in walk_report_leaves(pl_report):
        code = code_map.get(name) or _group_to_pnl_code(group, name)
        if code in (PNL_REVENUE, PNL_COGS, PNL_SM, PNL_RD, PNL_GA):
            agg[code] = round(agg.get(code, 0.0) + abs(amount), 2)
    return [{"entity_id": entity_id, "period": period,
             "account_code": code, "amount_local": agg[code]}
            for code in sorted(agg)]


def _group_to_bs_code(group, name):
    g, n = _lc(group), _lc(name)
    if "asset" in g:
        if "receivable" in n:
            return BS_AR
        if any(w in n for w in ("cash", "bank", "checking", "savings")):
            return BS_CASH
        return BS_FIXED
    if "liab" in g:
        return BS_DEFERRED if any(w in n for w in _DEFERRED_WORDS) else BS_AP
    if "equity" in g:
        return BS_RETAINED if any(w in n for w in _RETAINED_WORDS) or "net income" in n else BS_PAID_IN
    return None


def map_balance_sheet(bs_report, code_map, entity_id, period):
    """BalanceSheet report -> balance_sheet rows (positive magnitudes per code)."""
    agg = {}
    for group, name, amount in walk_report_leaves(bs_report):
        code = code_map.get(name) or _group_to_bs_code(group, name)
        if code:
            agg[code] = round(agg.get(code, 0.0) + amount, 2)
    return [{"entity_id": entity_id, "period": period,
             "account_code": code, "amount_local": agg[code]}
            for code in sorted(agg)]


def _status(balance):
    return PAID if _money(balance) <= 0.0 else OPEN


def _ref_name(obj, key):
    ref = (obj or {}).get(key) or {}
    return ref.get("name", "")


def _currency(obj):
    return (obj.get("CurrencyRef") or {}).get("value") or REPORTING_CURRENCY


def map_ar_invoices(invoices, entity_id):
    out = []
    for inv in invoices or []:
        out.append({
            "invoice_id": "INV-" + str(inv.get("Id", "")),
            "entity_id": entity_id,
            "customer": _ref_name(inv, "CustomerRef"),
            "currency": _currency(inv),
            "amount_local": _money(inv.get("TotalAmt")),
            "issue_date": inv.get("TxnDate", ""),
            "due_date": inv.get("DueDate", inv.get("TxnDate", "")),
            "status": _status(inv.get("Balance")),
        })
    return out


def map_ap_invoices(bills, entity_id):
    out = []
    for b in bills or []:
        out.append({
            "bill_id": "BILL-" + str(b.get("Id", "")),
            "entity_id": entity_id,
            "vendor": _ref_name(b, "VendorRef"),
            "currency": _currency(b),
            "amount_local": _money(b.get("TotalAmt")),
            "issue_date": b.get("TxnDate", ""),
            "due_date": b.get("DueDate", b.get("TxnDate", "")),
            "status": _status(b.get("Balance")),
        })
    return out


def map_payments(payments, bill_payments, entity_id):
    out = []
    for p in payments or []:
        out.append({
            "payment_id": "PMT-" + str(p.get("Id", "")), "entity_id": entity_id,
            "party": _ref_name(p, "CustomerRef"), "party_type": "customer",
            "currency": _currency(p), "amount_local": _money(p.get("TotalAmt")),
            "txn_date": p.get("TxnDate", ""), "applied_to": "AR"})
    for p in bill_payments or []:
        out.append({
            "payment_id": "BPMT-" + str(p.get("Id", "")), "entity_id": entity_id,
            "party": _ref_name(p, "VendorRef"), "party_type": "vendor",
            "currency": _currency(p), "amount_local": _money(p.get("TotalAmt")),
            "txn_date": p.get("TxnDate", ""), "applied_to": "AP"})
    return out


def map_customers(customers, entity_id):
    return [{"customer_id": "CUST-" + str(c.get("Id", "")), "entity_id": entity_id,
             "name": c.get("DisplayName") or c.get("CompanyName") or "",
             "currency": _currency(c), "balance": _money(c.get("Balance"))}
            for c in customers or []]


def map_vendors(vendors, entity_id):
    return [{"vendor_id": "VEND-" + str(v.get("Id", "")), "entity_id": entity_id,
             "name": v.get("DisplayName") or v.get("CompanyName") or "",
             "currency": _currency(v), "balance": _money(v.get("Balance"))}
            for v in vendors or []]


def map_journal_entries(journal_entries, accounts, entity_id, period):
    code_by_id = {str(a.get("Id")): canonical_code_for_account(a) for a in accounts or []}
    name_map = account_code_map(accounts)
    out = []
    for je in journal_entries or []:
        for line in je.get("Line", []):
            d = line.get("JournalEntryLineDetail")
            if not d:
                continue
            ref = d.get("AccountRef", {})
            code = code_by_id.get(str(ref.get("value"))) or name_map.get(ref.get("name", ""))
            if not code:
                continue
            amt = _money(line.get("Amount"))
            posting = _lc(d.get("PostingType"))
            out.append({
                "je_id": "JE-" + str(je.get("Id", "")), "entity_id": entity_id,
                "period": period, "account_code": code,
                "debit": amt if posting == "debit" else 0.0,
                "credit": amt if posting == "credit" else 0.0,
                "currency": _currency(je), "txn_date": je.get("TxnDate", "")})
    return out


def map_trial_balance(pnl_rows, bs_rows, entity_id, period):
    """Derive a PRE-CLOSING canonical trial balance from the rolled-up P&L and
    balance sheet.

    Normal-balance convention: assets and expenses are debits; liabilities,
    equity and income are credits. The balance sheet's retained earnings already
    folds in the current period's net income, so in a pre-closing trial balance
    (where the P&L accounts still hold that result) retained earnings is rolled
    BACK to its opening value; otherwise the result would be counted twice.
    Total debits then equal total credits whenever the balance sheet foots (the
    validation asserts this)."""
    from schema import (COA_TYPE, BS_RETAINED, PNL_REVENUE, PNL_COGS,
                        PNL_EXPENSE_CODES)
    debit_types = {"Asset", "Expense"}

    def _sum(rows, codes):
        return round(sum(float(r["amount_local"]) for r in rows if r["account_code"] in codes), 2)

    net_income = round(_sum(pnl_rows, (PNL_REVENUE,)) - _sum(pnl_rows, (PNL_COGS,))
                       - _sum(pnl_rows, PNL_EXPENSE_CODES), 2)

    rows = []
    for r in bs_rows + pnl_rows:
        code = r["account_code"]
        amt = round(float(r["amount_local"]), 2)
        if code == BS_RETAINED:
            amt = round(amt - net_income, 2)   # opening retained earnings (pre-closing)
        is_debit = COA_TYPE.get(code) in debit_types
        rows.append({"entity_id": entity_id, "period": period, "account_code": code,
                     "account_name": COA_NAME.get(code, ""),
                     "debit": amt if is_debit else 0.0,
                     "credit": 0.0 if is_debit else amt,
                     "currency": REPORTING_CURRENCY})
    return rows


# --------------------------------------------------------------------------
# Top-level: raw QBO responses -> full canonical table set
# --------------------------------------------------------------------------
def build_canonical(raw, entity_id, entity_name, period):
    """Transform one extraction's raw QBO responses into all canonical tables."""
    accounts = raw.get("accounts", [])
    code_map = account_code_map(accounts)

    pnl = map_pnl(raw.get("profit_and_loss"), code_map, entity_id, period)
    bs = map_balance_sheet(raw.get("balance_sheet"), code_map, entity_id, period)

    return {
        "entities": map_entities(entity_id, entity_name),
        "fx_rates": map_fx_rates([period]),
        "chart_of_accounts": map_chart_of_accounts(),
        "pnl_activity": pnl,
        "balance_sheet": bs,
        "budget": [],            # QBO sandbox has no budget object (documented)
        "ar_invoices": map_ar_invoices(raw.get("invoices"), entity_id),
        "ap_invoices": map_ap_invoices(raw.get("bills"), entity_id),
        "tax_obligations": [],   # no clean QBO tax-obligation source (documented)
        "trial_balance": map_trial_balance(pnl, bs, entity_id, period),
        "payments": map_payments(raw.get("payments"), raw.get("bill_payments"), entity_id),
        "customers": map_customers(raw.get("customers"), entity_id),
        "vendors": map_vendors(raw.get("vendors"), entity_id),
        "journal_entries": map_journal_entries(raw.get("journal_entries"), accounts, entity_id, period),
    }
