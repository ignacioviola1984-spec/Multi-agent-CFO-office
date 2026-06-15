"""
credit_core.py - Deterministic credit / loan-book engine for the LendingClub track.

Same philosophy as finance_core.py: this module computes the NUMBERS (no prose) so
the credit agents can reason and narrate without ever inventing a figure. One
source of data: ../lendingclub-data. The engine looks for the real Kaggle files
first and falls back to the seeded sample, so it runs today and points at real
data the moment the real CSVs are dropped in.

Function families (one per downstream agent):
  ingestion_summary()      -> Source Ingestion Agent
  data_quality()           -> Data Quality & Schema Agent
  provenance()             -> Source Traceability Agent
  portfolio_metrics()      -> Loan Portfolio Agent
  credit_risk()            -> Credit Risk / Losses Agent
  unit_economics()         -> Revenue & Unit Economics Agent
  benchmark_vs_filings()   -> Public Benchmark + Variance & Explainability Agents
  model_risk_review()      -> Model Risk / Audit Agent

Proxies (origination-fee rate, LGD floors, expected-loss formula) are documented
and conservative; they are clearly flagged as proxies for the model-risk layer.
"""

import csv
import os
import re

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lendingclub-data")

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
GRADES = ["A", "B", "C", "D", "E", "F", "G"]

# Documented PROXY: LendingClub origination fee by grade (a stand-in for the real
# fee schedule; flagged as a proxy to the model-risk layer).
_ORIG_FEE = {"A": 0.02, "B": 0.03, "C": 0.04, "D": 0.05, "E": 0.05, "F": 0.06, "G": 0.06}

# Statuses that represent a resolved (matured) outcome vs. still on the book.
_RESOLVED = {"Fully Paid", "Charged Off"}
_DELINQUENT = {"Late (31-120 days)", "Late (16-30 days)", "Default",
               "In Grace Period", "Charged Off"}


def _load_first(names):
    for n in names:
        p = os.path.join(DATA, n)
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f)), n
    return [], names[-1]


_ACC, ACCEPTED_FILE = _load_first(["accepted_2007_to_2018Q4.csv", "accepted_sample.csv"])
_REJ, REJECTED_FILE = _load_first(["rejected_2007_to_2018Q4.csv", "rejected_sample.csv"])
_FIL, FILINGS_FILE = _load_first(["public_filings.csv"])


# --- parsing helpers (tolerant; the real file is large and messy) ----------

def _f(x, default=0.0):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


def _rate(x):
    return _f(str(x).replace("%", "")) / 100.0


def _term(x):
    s = "".join(c for c in str(x) if c.isdigit())
    return int(s) if s else 0


def _year(issue_d):
    """Robust 4-digit year from LendingClub issue_d shapes: 'Dec-2015', 'Dec-15',
    bare '2015', ISO '2015-12'. Returns None if no year can be found."""
    s = str(issue_d).strip()
    m = re.search(r"(?:19|20)\d{2}", s)        # explicit 4-digit year anywhere
    if m:
        return int(m.group())
    m2 = re.search(r"-(\d{2})$", s)            # 'Mon-YY' two-digit year (LC is 2007+)
    if m2:
        return 2000 + int(m2.group(1))
    return None


# Real LC files prefix some terminal statuses with this; strip it so the
# resolved/charged/delinquent classification matches (e.g. "Does not meet the
# credit policy. Status:Charged Off" -> "Charged Off").
def _status(r):
    s = str(r.get("loan_status", "")).strip()
    if s.startswith("Does not meet the credit policy") and "Status:" in s:
        return s.split("Status:", 1)[1].strip()
    return s


# --- Source Ingestion ------------------------------------------------------

def ingestion_summary():
    """What was loaded, from which files, and the periods covered."""
    years = sorted({y for r in _ACC if (y := _year(r.get("issue_d"))) is not None})
    real = not ACCEPTED_FILE.endswith("_sample.csv")
    return {
        "accepted_file": ACCEPTED_FILE, "rejected_file": REJECTED_FILE,
        "filings_file": FILINGS_FILE if _FIL else None,
        "accepted_rows": len(_ACC), "rejected_rows": len(_REJ),
        "filing_rows": len(_FIL),
        "vintage_years": years, "is_real_data": real,
    }


# --- Data Quality & Schema -------------------------------------------------

_REQUIRED_ACC = ["id", "loan_amnt", "funded_amnt", "term", "int_rate", "grade",
                 "issue_d", "loan_status", "total_rec_prncp", "total_rec_int", "recoveries"]


def data_quality():
    """Schema + integrity checks on the accepted loan book. Each check is PASS or
    FAIL/WARN with a number behind it, so 'runs on real data' is provable."""
    checks = []
    cols = set(_ACC[0].keys()) if _ACC else set()
    missing_cols = [c for c in _REQUIRED_ACC if c not in cols]
    checks.append({"id": "DQ1", "name": "Required columns present",
                   "status": "PASS" if not missing_cols else "FAIL",
                   "detail": "all present" if not missing_cols else f"missing: {missing_cols}"})

    ids = [r.get("id") for r in _ACC if r.get("id")]
    dups = len(ids) - len(set(ids))
    checks.append({"id": "DQ2", "name": "No duplicate loan ids",
                   "status": "PASS" if dups == 0 else "FAIL",
                   "detail": f"{dups} duplicate id(s)"})

    n = len(_ACC) or 1
    key_cols = ["loan_amnt", "int_rate", "grade", "loan_status", "issue_d"]
    miss = {c: sum(1 for r in _ACC if not str(r.get(c, "")).strip()) for c in key_cols}
    worst = max(miss.values()) if miss else 0
    checks.append({"id": "DQ3", "name": "Missing values in key fields",
                   "status": "PASS" if worst == 0 else ("WARN" if worst / n < 0.05 else "FAIL"),
                   "detail": ", ".join(f"{c}={v}" for c, v in miss.items())})

    bad_dates = sum(1 for r in _ACC if _year(r.get("issue_d")) is None)
    checks.append({"id": "DQ4", "name": "Valid issue dates",
                   "status": "PASS" if bad_dates == 0 else "WARN",
                   "detail": f"{bad_dates} unparseable issue_d"})

    amts = [_f(r.get("loan_amnt")) for r in _ACC]
    outliers = sum(1 for a in amts if a <= 0 or a > 100000)
    checks.append({"id": "DQ5", "name": "Loan amount within bounds (0, 100k]",
                   "status": "PASS" if outliers == 0 else ("WARN" if outliers / n < 0.05 else "FAIL"),
                   "detail": f"{outliers} outlier amount(s)"})

    rate_bad = sum(1 for r in _ACC if not (0 < _rate(r.get("int_rate")) < 0.5))
    checks.append({"id": "DQ6", "name": "Interest rate within (0%, 50%)",
                   "status": "PASS" if rate_bad == 0 else ("WARN" if rate_bad / n < 0.05 else "FAIL"),
                   "detail": f"{rate_bad} out-of-range rate(s)"})

    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    return {"checks": checks, "n_pass": sum(1 for c in checks if c["status"] == "PASS"),
            "n_warn": n_warn, "n_fail": n_fail, "rows_checked": len(_ACC),
            "clean": n_fail == 0}


# --- Source Traceability ---------------------------------------------------

def provenance():
    """Which output traces to which file / columns / filter — the audit spine."""
    return {
        "source_file": ACCEPTED_FILE,
        "metrics": {
            "originations": {"file": ACCEPTED_FILE, "columns": ["funded_amnt", "issue_d"],
                             "filter": "all accepted loans"},
            "charge_off_rate": {"file": ACCEPTED_FILE, "columns": ["loan_status"],
                                "filter": "loan_status in {Fully Paid, Charged Off}"},
            "interest_income": {"file": ACCEPTED_FILE, "columns": ["total_rec_int"],
                                "filter": "all accepted loans"},
            "expected_loss": {"file": ACCEPTED_FILE,
                              "columns": ["funded_amnt", "total_rec_prncp", "grade", "loan_status"],
                              "filter": "on-book loans (Current/Late), PD by grade x LGD"},
            "approval_rate": {"file": f"{ACCEPTED_FILE} + {REJECTED_FILE}",
                              "columns": ["count"], "filter": "accepted / (accepted + rejected)"},
        },
        "proxies": {
            "origination_fee": "grade-based fee schedule (A 2% … G 6%) — PROXY, not a disclosure",
            "expected_loss": "realized PD by grade x LGD (1 - recovery rate) x outstanding — PROXY",
        },
    }


# --- Loan Portfolio --------------------------------------------------------

def portfolio_metrics():
    funded = [_f(r.get("funded_amnt")) for r in _ACC]
    total = sum(funded)
    n = len(_ACC) or 1
    wair = sum(_f(r.get("funded_amnt")) * _rate(r.get("int_rate")) for r in _ACC) / (total or 1)
    by_grade, by_term, by_year, by_status = {}, {}, {}, {}
    for r in _ACC:
        amt = _f(r.get("funded_amnt"))
        by_grade[r.get("grade", "?")] = by_grade.get(r.get("grade", "?"), 0.0) + amt
        by_term[_term(r.get("term"))] = by_term.get(_term(r.get("term")), 0.0) + amt
        y = _year(r.get("issue_d"))
        by_year[y] = by_year.get(y, 0.0) + amt
        s = _status(r) or "?"
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "n_loans": len(_ACC), "originations_usd": total, "avg_loan_usd": total / n,
        "wair": wair,
        "by_grade_usd": {g: by_grade.get(g, 0.0) for g in GRADES},
        "by_term_usd": by_term, "by_vintage_usd": by_year,
        "status_counts": by_status,
    }


# --- Credit Risk / Losses --------------------------------------------------

def credit_risk():
    matured = [r for r in _ACC if _status(r) in _RESOLVED]
    charged = [r for r in _ACC if _status(r) == "Charged Off"]
    n_mat = len(matured) or 1
    co_rate = len(charged) / n_mat

    # Realized PD and LGD by grade (from matured loans). Count how often the LGD
    # recovery-rate proxy had to be clamped to [0,1] (a data-quality signal).
    pd_grade, lgd_grade = {}, {}
    lgd_clamped = 0
    for g in GRADES:
        gm = [r for r in matured if r.get("grade") == g]
        gc = [r for r in gm if _status(r) == "Charged Off"]
        pd_grade[g] = (len(gc) / len(gm)) if gm else 0.0
        # LGD = 1 - recovery rate on the charged-off principal.
        co_prncp = sum(_f(r.get("funded_amnt")) - _f(r.get("total_rec_prncp")) for r in gc)
        recov = sum(_f(r.get("recoveries")) for r in gc)
        if co_prncp > 0:
            raw = 1 - (recov / co_prncp)
            if raw < 0 or raw > 1:
                lgd_clamped += 1
            lgd_grade[g] = max(0.0, min(1.0, raw))
        else:
            lgd_grade[g] = 0.55

    # Expected loss on the ON-BOOK loans (not yet resolved): outstanding x PD x LGD.
    onbook = [r for r in _ACC if _status(r) not in _RESOLVED]
    el = 0.0
    outstanding_total = 0.0
    for r in onbook:
        outstanding = max(0.0, _f(r.get("funded_amnt")) - _f(r.get("total_rec_prncp")))
        outstanding_total += outstanding
        g = r.get("grade", "?")
        el += outstanding * pd_grade.get(g, co_rate) * lgd_grade.get(g, 0.55)

    delinquent = [r for r in _ACC if _status(r) in _DELINQUENT and _status(r) != "Charged Off"]
    charged_off_usd = sum(_f(r.get("funded_amnt")) - _f(r.get("total_rec_prncp")) for r in charged)

    return {
        "n_matured": len(matured), "n_charged_off": len(charged),
        "charge_off_rate": co_rate, "charged_off_usd": charged_off_usd,
        "pd_by_grade": pd_grade, "lgd_by_grade": lgd_grade,
        "lgd_clamped_grades": lgd_clamped,
        "n_onbook": len(onbook), "onbook_outstanding_usd": outstanding_total,
        "expected_loss_usd": el,
        "expected_loss_pct": (el / outstanding_total) if outstanding_total else 0.0,
        "n_delinquent": len(delinquent),
        "delinquency_rate": len(delinquent) / (len(onbook) or 1),
    }


# --- Revenue & Unit Economics ----------------------------------------------

def unit_economics():
    funded = sum(_f(r.get("funded_amnt")) for r in _ACC)
    int_income = sum(_f(r.get("total_rec_int")) for r in _ACC)
    fees = sum(_f(r.get("funded_amnt")) * _ORIG_FEE.get(r.get("grade"), 0.04) for r in _ACC)
    net_cash = sum(_f(r.get("total_pymnt")) - _f(r.get("funded_amnt")) for r in _ACC)
    # Cohort (vintage) profitability: cash-on-cash so far per issue year.
    coh = {}
    for r in _ACC:
        y = _year(r.get("issue_d"))
        c = coh.setdefault(y, {"funded": 0.0, "received": 0.0})
        c["funded"] += _f(r.get("funded_amnt"))
        c["received"] += _f(r.get("total_pymnt"))
    cohorts = {y: {"funded_usd": v["funded"], "received_usd": v["received"],
                   "net_usd": v["received"] - v["funded"],
                   "cash_on_cash": (v["received"] / v["funded"]) if v["funded"] else 0.0}
               for y, v in coh.items()}
    return {
        "interest_income_usd": int_income, "origination_fees_usd": fees,
        "total_revenue_proxy_usd": int_income + fees,
        "yield_realized": (int_income / funded) if funded else 0.0,
        "take_rate": (fees / funded) if funded else 0.0,
        "net_cash_to_date_usd": net_cash,
        "cohorts": cohorts,
    }


# --- Rejection / approval --------------------------------------------------

def approval_metrics():
    na, nr = len(_ACC), len(_REJ)
    total = na + nr
    return {"accepted": na, "rejected": nr,
            "approval_rate": (na / total) if total else 0.0,
            "avg_requested_rejected_usd": (sum(_f(r.get("Amount Requested")) for r in _REJ) / nr)
            if nr else 0.0}


# --- Public Benchmark + Variance -------------------------------------------

def _computed_for(metric, period):
    """Compute a metric for a given filing period (year as string, or 'ALL')."""
    rows = _ACC if period in ("ALL", "", None) else [
        r for r in _ACC if str(_year(r.get("issue_d"))) == str(period)]
    if metric == "originations_usd":
        return sum(_f(r.get("funded_amnt")) for r in rows)
    if metric == "interest_income_usd":
        return sum(_f(r.get("total_rec_int")) for r in rows)
    if metric == "loan_count":
        return float(len(rows))
    if metric == "avg_interest_rate":
        f = sum(_f(r.get("funded_amnt")) for r in rows) or 1
        return sum(_f(r.get("funded_amnt")) * _rate(r.get("int_rate")) for r in rows) / f
    if metric == "charge_off_rate":
        mat = [r for r in rows if _status(r) in _RESOLVED]
        co = [r for r in mat if _status(r) == "Charged Off"]
        return (len(co) / len(mat)) if mat else 0.0
    return None


def benchmark_vs_filings():
    """Compare computed KPIs to the public-filing values, with variance. Skips
    metrics the engine can't compute or filings it doesn't have."""
    out = []
    for r in _FIL:
        metric, period = r.get("metric"), r.get("period")
        filed = _f(r.get("value"))
        computed = _computed_for(metric, period)
        if computed is None:
            continue
        var = computed - filed
        var_pct = (var / filed * 100) if filed else 0.0
        out.append({"metric": metric, "period": period, "filed": filed,
                    "computed": computed, "var": var, "var_pct": var_pct,
                    "source_doc": r.get("source_doc", ""), "note": r.get("note", "")})
    return {"rows": out, "n": len(out),
            "max_abs_var_pct": max((abs(x["var_pct"]) for x in out), default=0.0)}


# --- Model Risk / Audit ----------------------------------------------------

def model_risk_review():
    """Red-flag review of the credit MODEL itself (not a re-run of other functions'
    risks): data realness, reliance on documented proxies, and proxy stress.
    Data-quality failures and benchmark drift are owned and escalated by the Data
    Quality and Variance functions and are NOT re-escalated here, so the CFO sees
    each risk from a single owner."""
    ing = ingestion_summary()
    cr = credit_risk()
    flags = []
    if not ing["is_real_data"]:
        flags.append(["HIGH", "running on the seeded SAMPLE, not the real LendingClub files"])
    flags.append(["MEDIUM", "expected-loss and fee figures use documented PROXIES, not disclosures"])
    if not _FIL:
        flags.append(["MEDIUM", "no public-filing benchmark loaded (public_filings.csv empty)"])
    if cr.get("lgd_clamped_grades", 0) > 0:
        flags.append(["MEDIUM",
                      f"LGD recovery rate clamped for {cr['lgd_clamped_grades']} grade(s) — possible data issue"])
    return {"flags": flags, "n_flags": len(flags),
            "assumptions": ["realized PD by grade", "LGD = 1 - recovery rate",
                            "origination fee = grade-based proxy",
                            "Default and other late statuses are treated as on-book exposures "
                            "(outstanding x PD x LGD), not as realized/near-certain losses"],
            "limitations": ["sample unless real files dropped",
                            "data-quality failures and benchmark drift are owned by the Data "
                            "Quality and Variance functions",
                            "no macroeconomic overlay", "no forward-looking ECL staging"]}
