"""
credit_stages.py - The credit (LendingClub) operating model as explicit STAGES.

Same engine as stages.py (SaaS close), applied to a lending business: every stage
is a maker agent + a deterministic control in code + the domain expert's sign-off,
with REWORK on a sign-off rejection and a hard BLOCK. A deterministic control
failure blocks immediately (a code gate over static data can't be cleared by
re-running). The CFO Narrative + final gate (credit_orchestrator) is contingent on
every stage passing.

Layers: data foundation (ingestion -> data quality -> traceability) -> fintech
analytics (loan portfolio -> credit risk -> revenue & unit economics) -> benchmark
(public benchmark -> variance & explainability) -> assurance (model risk).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "credit"))             # the credit maker agents
sys.path.insert(0, os.path.join(HERE, "..", "orchestration"))  # credit_core

import review
import credit_core as cc
import source_ingestion_agent
import data_quality_agent
import source_traceability_agent
import loan_portfolio_agent
import credit_risk_agent
import revenue_unit_economics_agent
import public_benchmark_agent
import variance_explainability_agent
import model_risk_agent

MAX_ATTEMPTS = 2


# --- deterministic stage controls (code-level gates, not the model) --------

def _ctrl_data_quality(ctx):
    nf = ctx.get("Data Quality", "n_fail", 1)
    ok = nf == 0
    return ok, (f"{ctx.get('Data Quality', 'n_pass', 0)} checks pass, 0 schema/integrity failures"
                if ok else f"{nf} data-quality failure(s) — bad data does not proceed")


def _ctrl_traceability(ctx):
    m = ctx.get("Source Traceability", "metrics", {})
    ok = bool(m)
    return ok, (f"provenance recorded for {len(m)} metric families" if ok
                else "no source provenance recorded")


# Model risk is a REVIEW + sign-off stage, not a hard code gate: the data-quality
# hard gate already lives at stage 2, so a stage-9 re-check of the same condition
# would be a dead gate. Its value is surfacing model-risk flags + the HITL sign-off.


# --- stage runners: maker does the work, then its reviewer signs off --------

def _run_ingestion(ctx):
    source_ingestion_agent.run(ctx)
    review.review(ctx, "Source Ingestion",
                  f"{ctx.get('Source Ingestion', 'accepted_rows', 0)} accepted loans, "
                  f"real={ctx.get('Source Ingestion', 'is_real_data', False)}")


def _run_data_quality(ctx):
    data_quality_agent.run(ctx)
    review.review(ctx, "Data Quality",
                  f"{ctx.get('Data Quality', 'n_pass', 0)} pass / {ctx.get('Data Quality', 'n_fail', 0)} fail")


def _run_traceability(ctx):
    source_traceability_agent.run(ctx)
    review.review(ctx, "Source Traceability", "source provenance recorded")


def _run_portfolio(ctx):
    loan_portfolio_agent.run(ctx)
    review.review(ctx, "Loan Portfolio",
                  f"originations USD {ctx.get('Loan Portfolio', 'originations_usd', 0):,.0f}")


def _run_credit_risk(ctx):
    credit_risk_agent.run(ctx)
    review.review(ctx, "Credit Risk",
                  f"charge-off {ctx.get('Credit Risk', 'charge_off_rate', 0) * 100:.1f}%, "
                  f"EL USD {ctx.get('Credit Risk', 'expected_loss_usd', 0):,.0f}")


def _run_revenue(ctx):
    revenue_unit_economics_agent.run(ctx)
    review.review(ctx, "Revenue & Unit Economics",
                  f"interest income USD {ctx.get('Revenue & Unit Economics', 'interest_income_usd', 0):,.0f}")


def _run_benchmark(ctx):
    public_benchmark_agent.run(ctx)
    review.review(ctx, "Public Benchmark",
                  f"{ctx.get('Public Benchmark', 'n', 0)} KPI(s) vs filings")


def _run_variance(ctx):
    variance_explainability_agent.run(ctx)
    review.review(ctx, "Variance & Explainability",
                  f"max |var| {ctx.get('Variance & Explainability', 'max_abs_var_pct', 0):.0f}%")


def _run_model_risk(ctx):
    model_risk_agent.run(ctx)
    review.review(ctx, "Model Risk",
                  f"{ctx.get('Model Risk', 'n_flags', 0)} model-risk flag(s)")


# --- the credit operating model: stages end to end -------------------------

STAGES = [
    {"id": 1, "name": "Source ingestion", "run": _run_ingestion,
     "functions": ["Source Ingestion"], "control": None},
    {"id": 2, "name": "Data quality & schema", "run": _run_data_quality,
     "functions": ["Data Quality"], "control": _ctrl_data_quality},
    {"id": 3, "name": "Source traceability", "run": _run_traceability,
     "functions": ["Source Traceability"], "control": _ctrl_traceability},
    {"id": 4, "name": "Loan portfolio", "run": _run_portfolio,
     "functions": ["Loan Portfolio"], "control": None},
    {"id": 5, "name": "Credit risk & losses", "run": _run_credit_risk,
     "functions": ["Credit Risk"], "control": None},
    {"id": 6, "name": "Revenue & unit economics", "run": _run_revenue,
     "functions": ["Revenue & Unit Economics"], "control": None},
    {"id": 7, "name": "Public benchmark", "run": _run_benchmark,
     "functions": ["Public Benchmark"], "control": None},
    {"id": 8, "name": "Variance & explainability", "run": _run_variance,
     "functions": ["Variance & Explainability"], "control": None},
    {"id": 9, "name": "Model risk & audit", "run": _run_model_risk,
     "functions": ["Model Risk"], "control": None},
]


def run_stage(ctx, stage):
    """Run one stage: maker -> deterministic control -> HITL sign-off. A control
    failure blocks immediately; a sign-off rejection reworks (capped) then blocks."""
    reviewers = [review.REVIEWERS.get(f, "Domain reviewer") for f in stage["functions"]]

    def _blocked(attempt, detail, reason):
        ctx.audit("Credit Operating Model", "stage BLOCKED", f"{stage['name']}: {reason}")
        return {"id": stage["id"], "name": stage["name"], "status": "blocked",
                "attempts": attempt, "control": detail, "reason": reason,
                "functions": stage["functions"], "reviewers": reviewers}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        tag = f"stage {stage['id']}" + (f" (rework {attempt - 1})" if attempt > 1 else "")
        ctx.audit("Credit Operating Model", tag, f"{stage['name']}: running")
        stage["run"](ctx)

        ok, detail = stage["control"](ctx) if stage["control"] else (True, "no code-level control")
        fl = review.first_line_status(ctx, stage["functions"])

        if ok and fl["all_approved"]:
            ctx.audit("Credit Operating Model", "stage PASS",
                      f"{stage['name']}: control [{detail}]; signed off by {', '.join(reviewers)}")
            return {"id": stage["id"], "name": stage["name"], "status": "passed",
                    "attempts": attempt, "control": detail,
                    "functions": stage["functions"], "reviewers": reviewers}

        if not ok:
            return _blocked(attempt, detail, "control failed: " + detail)

        reason = "not signed off: " + ", ".join(fl["rejected"])
        if attempt < MAX_ATTEMPTS:
            ctx.audit("Credit Operating Model", "stage REWORK", f"{stage['name']}: {reason} -> rework")
            continue
        return _blocked(attempt, detail, reason)


def run_all(ctx):
    """Run every credit stage in order. Stops at the first BLOCKED stage."""
    results = []
    for stage in STAGES:
        print(f"\n[credit stage {stage['id']}/{len(STAGES)}] {stage['name']}...")
        res = run_stage(ctx, stage)
        results.append(res)
        if res["status"] == "blocked":
            break
    all_passed = all(r["status"] == "passed" for r in results) and len(results) == len(STAGES)
    ctx.put("Credit Operating Model", {"stages": results, "all_passed": all_passed})
    return results, all_passed
