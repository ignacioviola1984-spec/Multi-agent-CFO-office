"""
o2c_orchestrator.py - The Order-to-Cash control-tower orchestrator.

Runs the O2C operating model end to end:
  load -> validate schema -> deterministic calculations -> controls ->
  agents (makers) + maker/checker sign-off (HITL) -> hard gate on reporting ->
  metrics -> executive summary, board pack, workflow map -> audit trail.

This is a sub-orchestrator under the CFO Office: it owns Revenue Operations and
Order-to-Cash, the same way cfo_orchestrator.py owns the month-end close. Numbers
are deterministic (o2c_core); agents diagnose and narrate but never invent a
number. Hard control failures BLOCK the release of O2C reporting.

CLI:
  python cfo-office/o2c/o2c_orchestrator.py --period 2026-05
  python cfo-office/o2c/o2c_orchestrator.py --period 2026-05 --fail-on-hard-controls
  python cfo-office/o2c/o2c_orchestrator.py --period 2026-05 --output-dir cfo-office/o2c/outputs
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.join(HERE, "agents")
for _p in (HERE, AGENTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

import o2c_policy as P
import o2c_core as core
import o2c_controls as controls
import o2c_metrics as metrics
import o2c_data_loader as loader
import base_agent
from base_agent import O2CContext

import order_intake_agent
import customer_master_agent
import contract_agent
import billing_agent
import revenue_recognition_agent
import collections_agent
import cash_application_agent
import disputes_credit_agent
import revops_analytics_agent
import o2c_audit_agent

AGENT_MODULES = [
    order_intake_agent, customer_master_agent, contract_agent, billing_agent,
    revenue_recognition_agent, collections_agent, cash_application_agent,
    disputes_credit_agent, revops_analytics_agent, o2c_audit_agent,
]
DEFAULT_OUTPUT_DIR = os.path.join(HERE, "outputs")


# --------------------------------------------------------------------------
# Maker / checker (HITL). Auto-approves in non-interactive runs and records it
# AS 'auto' - it is never passed off as a real human sign-off (same posture as
# cfo-office/review.py).
# --------------------------------------------------------------------------
def _auto_review():
    """Auto-approve (recorded AS 'auto') by default so batch/CI/demo runs never
    hang. Only prompt a real human when O2C_INTERACTIVE is set and there is a tty.
    An 'auto' record is never passed off as a real human sign-off."""
    if not os.environ.get("O2C_INTERACTIVE"):
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def maker_checker(ctx, agent_name, headline):
    checker = P.CHECKER_OWNERS.get(agent_name, "Reviewer")
    if _auto_review():
        decision, mode = "approved", "auto"
    else:
        try:
            ans = input(f"  [{checker}] sign off {agent_name} ({headline})? [y]es/[n]o: ").strip().lower()
        except EOFError:
            ans = ""
        decision = "approved" if ans in ("y", "yes") else "rejected"
        mode = "human"
    rec = {"agent": agent_name, "checker": checker, "decision": decision, "mode": mode,
           "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    ctx.reviews[agent_name] = rec
    ctx.record(checker, decision.upper(), f"{agent_name} {decision}"
               + (" (auto)" if mode == "auto" else ""))
    return rec


# --------------------------------------------------------------------------
# Calculations shared by all agents
# --------------------------------------------------------------------------
def build_calc(dfs, period):
    controls_results = controls.run_all_controls(dfs, period)
    summary = core.build_executive_o2c_summary(dfs, period)
    return {
        "summary": summary,
        "aging": core.calculate_ar_aging(dfs, period),
        "chain": core.build_opportunity_to_cash_chain(dfs, period),
        "controls": controls_results,
        "controls_summary": controls.controls_summary(controls_results),
        "metrics": metrics.build_metrics(dfs, period, controls_results, summary),
        "bridge": core.build_bookings_billings_revenue_cash_bridge(dfs, period),
        "open_items": core.calculate_ar_open_items(dfs, period),
    }


# --------------------------------------------------------------------------
# JSON helper
# --------------------------------------------------------------------------
def _json_default(o):
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:                              # pragma: no cover
            pass
    return str(o)


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=_json_default)


# --------------------------------------------------------------------------
# Markdown composition
# --------------------------------------------------------------------------
def _m(x):
    return f"USD {x:,.0f}"


def compose_executive_summary(ctx, status):
    s = ctx.calc["summary"]
    csum = ctx.calc["controls_summary"]
    issues = ctx.escalations()[:10]
    health = {"BLOCKED_HARD_CONTROLS": "BLOCKED - hard controls failing",
              "PASS_WITH_WARNINGS": "PASS WITH WARNINGS",
              "PASS": "HEALTHY"}[status]
    lines = [f"# O2C Executive Summary - {s['period']}", "",
             f"**O2C health:** {health}  |  **Control pass rate:** {csum['control_pass_rate_pct']}%"
             f"  |  **Hard control failures:** {csum['hard_failures']}", ""]
    lines += ["## Headline numbers (USD)", "",
              "| Metric | Value |", "|---|---|",
              f"| Open AR | {_m(s['open_ar_usd'])} |",
              f"| Overdue AR | {_m(s['overdue_ar_usd'])} |",
              f"| DSO (best possible) | {s['dso']} days ({s['best_possible_dso']}) |",
              f"| Expected cash 13 weeks | {_m(s['expected_cash_13w_usd'])} |",
              f"| Unbilled revenue / leakage | {_m(s['unbilled_revenue_usd'])} |",
              f"| Unapplied cash | {_m(s['unapplied_cash_usd'])} |",
              f"| Disputed AR ({s['disputed_ar_pct']}%) | {_m(s['disputed_ar_usd'])} |",
              f"| Credit exposure (breach) | {_m(s['credit_exposure_usd'])} ({_m(s['credit_breach_amount_usd'])}) |",
              f"| Billing completeness / accuracy | {s['billing_completeness_pct']}% / {s['invoice_accuracy_pct']}% |",
              ""]
    lines += ["## Top 10 issues", ""]
    if issues:
        for i, e in enumerate(issues, 1):
            lines.append(f"{i}. [{e['severity']}] ({e['agent']}) {e['message']}")
    else:
        lines.append("None.")
    lines += ["", "## Recommended leadership actions", ""]
    actions = []
    for agent, f in ctx.findings.items():
        actions.extend(f.get("recommended_actions", []))
    for a in actions[:10]:
        lines.append(f"- {a}")
    lines += ["", "## Required human approvals (maker/checker)", ""]
    for agent, rec in ctx.reviews.items():
        lines.append(f"- {agent} -> {rec['checker']}: {rec['decision']}"
                     + (" (auto, non-interactive)" if rec["mode"] == "auto" else " (human)"))
    lines.append("- Final gate: Controller / CFO sign-off on the consolidated O2C pack.")
    if status == "BLOCKED_HARD_CONTROLS":
        lines += ["", "> Reporting is BLOCKED until the hard control failures above are cleared."]
    return "\n".join(lines) + "\n"


def compose_board_pack(ctx, status):
    s = ctx.calc["summary"]
    bridge = ctx.calc["bridge"]
    aging = ctx.calc["aging"]
    revops = ctx.get("RevOpsAnalyticsAgent")
    billing = ctx.get("BillingAgent")
    rev = ctx.get("RevenueRecognitionAgent")
    cash = ctx.get("CashApplicationAgent")
    audit = ctx.get("O2CAuditAgent")
    hard_fail = [r for r in ctx.calc["controls"] if r.severity == "HARD" and r.status == "FAIL"]

    lines = [f"# O2C Board Pack - {s['period']}", "",
             "## Executive narrative", "", revops.get("board_narrative", ""), "",
             "## Bookings -> Billings -> Revenue -> Cash bridge (USD)", "",
             "| Stage | Amount |", "|---|---|"]
    for label, amt in bridge["bridge"]:
        lines.append(f"| {label} | {_m(amt)} |")
    lines += ["", "## AR aging (USD)", "", "| Bucket | Open AR | Invoices |", "|---|---|---|"]
    for r in aging["by_bucket"].to_dict("records"):
        lines.append(f"| {r['aging_bucket']} | {_m(r['open_ar_usd'])} | {r['invoice_count']} |")
    lines += ["", "## Collections risk", "",
              f"- DSO {s['dso']} days (best possible {s['best_possible_dso']}); "
              f"overdue {_m(s['overdue_ar_usd'])}, 90+ {_m(s['ar_90_plus_usd'])}.",
              f"- Broken promises to pay: {_m(s['broken_promise_amount_usd'])}.",
              "", "## Billing accuracy & leakage", "",
              f"- Completeness {s['billing_completeness_pct']}%, accuracy {s['invoice_accuracy_pct']}%, "
              f"timeliness {s['billing_timeliness_pct']}%.",
              f"- Unbilled revenue (leakage): {_m(s['unbilled_revenue_usd'])}.",
              "", "## Revenue recognition issues", "",
              f"- Cutoff exceptions and deferred breaks flagged: "
              f"{rev.get('accounting_review_queue_size', 0)} items for accounting review.",
              "", "## Cash application issues", "",
              f"- Application rate {s['cash_application_rate_pct']}%; unapplied {_m(s['unapplied_cash_usd'])}; "
              f"treasury tie-out {'in balance' if cash.get('treasury_tie_out', {}).get('in_balance') else 'OUT OF BALANCE'}.",
              "", "## Control failures (block reporting)", ""]
    if hard_fail:
        lines += ["| Control | Failing | Amount (USD) | Owner |", "|---|---|---|---|"]
        for r in hard_fail:
            lines.append(f"| {r.control_id} | {r.failing_record_count} | {_m(r.failing_amount_usd)} | {r.owner} |")
    else:
        lines.append("None.")
    lines += ["", f"**Independent audit opinion:** {audit.get('audit_opinion', 'n/a').upper()} "
              f"(score {audit.get('audit_score', 0)}%).", "",
              "## Owner / action / date", "", "| Owner | Action | Due |", "|---|---|---|"]
    due = (core.as_of_date(s["period"]) + pd.Timedelta(days=7)).date()
    seen = set()
    for agent, f in ctx.findings.items():
        for a in f.get("recommended_actions", []):
            if a in seen:
                continue
            seen.add(a)
            lines.append(f"| {f.get('maker_owner', agent)} | {a} | {due} |")
    return "\n".join(lines) + "\n"


def compose_workflow_map(ctx, status):
    s = ctx.calc["summary"]
    lines = [f"# O2C Agentic Workflow Map - {s['period']}", "",
             "## Current-state pain points", "",
             "- Manual reconciliation across CRM, ERP, billing, and bank, with no single tie-out.",
             "- Revenue leakage from unbilled work and invoice errors caught late or not at all.",
             "- Cash stuck in unapplied receipts and disputes, inflating DSO.",
             "- Credit limits and holds enforced inconsistently across regions and entities.",
             "- Reporting released before subledgers tie to the control accounts.",
             "", "## Future-state agentic workflow (this module)", "",
             "Agent-first, human-led. Deterministic calculations; agents diagnose, prioritize,",
             "explain, route, and draft; humans approve at maker/checker checkpoints; hard",
             "controls block reporting.", "",
             "```",
             "CRM -> Customer Master -> Contracts -> Orders -> Billing -> Invoices -> Revenue",
             "                                                   |", "                                                   v",
             "                          AR -> Collections -> Cash Application -> Bank -> GL / Reporting",
             "```", "",
             "## Source systems", "",
             "Salesforce (CRM), CLM (contracts), ERP (orders/billing/invoices), bank files,",
             "revenue subledger, credit system.", "",
             "## Agents (makers) and their checkers", "", "| Agent | Checker (HITL) |", "|---|---|"]
    for agent in P.CHECKER_OWNERS:
        lines.append(f"| {agent} | {P.CHECKER_OWNERS[agent]} |")
    lines += ["", "## Deterministic controls", "",
              f"- {len([r for r in ctx.calc['controls'] if r.severity == 'HARD'])} HARD controls "
              f"(block reporting), {len([r for r in ctx.calc['controls'] if r.severity == 'SOFT'])} SOFT controls (warn).",
              "- A HARD failure stops the release of O2C reporting until cleared.",
              "", "## Human checkpoints", "",
              "1. First line: each agent's finding is signed off by its domain-expert checker.",
              "2. Hard gate: all hard controls must pass before reporting is released.",
              "3. Final gate: Controller / CFO sign-off on the consolidated O2C pack.",
              "", "## Escalation path", ""]
    for k, v in P.ESCALATION_PATH.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Output artifacts", "",
              "`o2c_control_results.csv`, `o2c_metrics.csv`, `o2c_exceptions.csv`,",
              "`o2c_agent_findings.json`, `o2c_audit_trail.json`, `o2c_executive_summary.md`,",
              "`o2c_board_pack.md`, `o2c_workflow_map.md`."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
def _git_commit_hash():
    """The git commit this run executed against (like `git rev-parse HEAD`), with a
    '-dirty' suffix when tracked files have uncommitted changes. Returns 'unknown'
    if git is not available or the command fails. Untracked files do NOT count as
    dirty (matches `git describe --dirty`), so generated outputs never mark a run
    dirty. Uses subprocess only; no dependencies."""
    def _git(args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                              text=True, timeout=5)
    try:
        head = _git(["rev-parse", "HEAD"])
        sha = head.stdout.strip()
        if head.returncode != 0 or not sha:
            return "unknown"
        status = _git(["status", "--porcelain", "--untracked-files=no"])
        dirty = status.returncode == 0 and status.stdout.strip() != ""
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _copy_to_latest(src_dir, latest_dir):
    """Mirror a run's output files into a stable `latest/` directory (overwriting),
    so consumers that read a fixed path always find the newest run. Copies the
    flat output files only; same names, same content, only the location differs."""
    os.makedirs(latest_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        sp = os.path.join(src_dir, name)
        if os.path.isfile(sp):
            shutil.copy2(sp, os.path.join(latest_dir, name))
    return latest_dir


def write_outputs(ctx, status, run_meta, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    paths = {}

    controls.results_to_dataframe(ctx.calc["controls"]).to_csv(
        os.path.join(output_dir, "o2c_control_results.csv"), index=False)
    paths["o2c_control_results.csv"] = "control results"

    metrics.metrics_to_dataframe(ctx.calc["metrics"]).to_csv(
        os.path.join(output_dir, "o2c_metrics.csv"), index=False)
    paths["o2c_metrics.csv"] = "metrics"

    exc = controls.results_to_dataframe(
        [r for r in ctx.calc["controls"] if r.status in ("FAIL", "WARNING")])
    exc.to_csv(os.path.join(output_dir, "o2c_exceptions.csv"), index=False)
    paths["o2c_exceptions.csv"] = "exceptions"

    _dump(ctx.findings, os.path.join(output_dir, "o2c_agent_findings.json"))
    paths["o2c_agent_findings.json"] = "agent findings"

    _dump({"run": run_meta, "audit_trail": ctx.audit, "reviews": ctx.reviews},
          os.path.join(output_dir, "o2c_audit_trail.json"))
    paths["o2c_audit_trail.json"] = "audit trail"

    for fname, text in [("o2c_executive_summary.md", compose_executive_summary(ctx, status)),
                        ("o2c_board_pack.md", compose_board_pack(ctx, status)),
                        ("o2c_workflow_map.md", compose_workflow_map(ctx, status))]:
        with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
            f.write(text)
        paths[fname] = fname

    return paths


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------
def run(period=P.DEFAULT_PERIOD, output_dir=DEFAULT_OUTPUT_DIR, fail_on_hard=False, verbose=True,
        history_run_id=None, history_subdir=None):
    started = datetime.datetime.now()
    run_id = f"O2C-{period}-{started.strftime('%Y%m%d%H%M%S')}"
    # When a history_run_id is given, write this run's outputs to a per-run folder
    # (<output_dir>/runs/<run_id>/[<subdir>/]) and mirror them to
    # <output_dir>/latest/[<subdir>/] after, so runs are preserved and a stable
    # path still points at the newest. history_subdir nests the run by period,
    # used by --compare which archives several periods under ONE run_id. Without a
    # history_run_id, behaviour is unchanged: outputs go straight to output_dir.
    if history_run_id:
        rel = os.path.join(history_run_id, history_subdir) if history_subdir else history_run_id
        target_dir = os.path.join(output_dir, "runs", rel)
        latest_target = os.path.join(output_dir, "latest", history_subdir) if history_subdir \
            else os.path.join(output_dir, "latest")
    else:
        target_dir, latest_target = output_dir, None
    if verbose:
        print("=" * 64)
        print(f"O2C CONTROL TOWER | period {period} | run {run_id}")
        print("=" * 64)

    dfs = core.load(period)                             # load + validate + normalize
    if verbose:
        print(f"  loaded {len(dfs)} datasets, "
              f"{sum(len(d) for d in dfs.values()):,} records total")

    calc = build_calc(dfs, period)
    ctx = O2CContext(dfs, period, calc)
    ctx.record("orchestrator", "start", f"O2C control tower for {period}")

    # run agents (makers) + first-line checker sign-off
    for mod in AGENT_MODULES:
        findings = mod.run(ctx)
        maker_checker(ctx, findings["agent"], findings.get("headline", ""))
        if verbose:
            print(f"  [{findings['agent']:24}] {findings.get('headline', '')}")

    csum = calc["controls_summary"]
    if csum["hard_failures"] > 0:
        status = "BLOCKED_HARD_CONTROLS"
    elif csum["soft_warnings"] > 0:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    audit = ctx.get("O2CAuditAgent")
    run_meta = {
        "run_id": run_id, "commit_hash": _git_commit_hash(),
        "run_timestamp": started.isoformat(timespec="seconds"), "period": period,
        "input_files": list(loader.FILES.values()),
        "input_record_counts": {k: int(len(v)) for k, v in dfs.items()},
        "calculations_performed": [
            "open AR & aging", "billing completeness/accuracy/timeliness", "cash application",
            "revenue recognition & deferred rollforward", "credit exposure", "collections forecast",
            "bookings->billings->revenue->cash bridge", "DSO"],
        "controls_run": [r.control_id for r in calc["controls"]],
        "agents_run": [m.run.__module__ for m in AGENT_MODULES],
        "hard_failures": csum["hard_failure_ids"], "soft_warnings": csum["soft_warnings"],
        "human_approvals_required": {a: P.CHECKER_OWNERS[a] for a in P.CHECKER_OWNERS},
        "audit_score": audit.get("audit_score"), "audit_opinion": audit.get("audit_opinion"),
        "final_status": status,
    }
    if history_run_id:
        run_meta["run_dir"] = history_run_id   # the run-folder id, recorded in the audit trail

    ctx.record("orchestrator", "gate",
               f"hard failures={csum['hard_failures']} -> status {status}")
    paths = write_outputs(ctx, status, run_meta, target_dir)
    run_meta["output_files"] = list(paths.keys())
    _dump({"run": run_meta, "audit_trail": ctx.audit, "reviews": ctx.reviews},
          os.path.join(target_dir, "o2c_audit_trail.json"))   # rewrite with output_files
    latest_dir = _copy_to_latest(target_dir, latest_target) if history_run_id else None

    if verbose:
        print("-" * 64)
        print(f"  controls: {csum['hard_failures']} hard fail, {csum['soft_warnings']} soft warn, "
              f"pass rate {csum['control_pass_rate_pct']}%")
        print(f"  audit opinion: {audit.get('audit_opinion', 'n/a').upper()} "
              f"(score {audit.get('audit_score')}%)")
        print(f"  FINAL STATUS: {status}")
        print(f"  outputs written to: {target_dir}")
        if latest_dir:
            print(f"  latest copy: {latest_dir}")
        for fn in paths:
            print(f"    - {fn}")

    if fail_on_hard and csum["hard_failures"] > 0:
        if verbose:
            print("\n  --fail-on-hard-controls set and hard controls failed: exit 1")
        sys.exit(1)
    return ctx, run_meta


def main():
    ap = argparse.ArgumentParser(description="O2C / RevOps control tower")
    ap.add_argument("--period", default=P.DEFAULT_PERIOD, help="reporting period YYYY-MM")
    ap.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="where to write outputs")
    ap.add_argument("--fail-on-hard-controls", action="store_true",
                    help="exit non-zero if any hard control fails (CI gate)")
    args = ap.parse_args()
    run(period=args.period, output_dir=args.output_dir, fail_on_hard=args.fail_on_hard_controls)


if __name__ == "__main__":
    main()
