"""
run_o2c_control_tower.py - One-command entrypoint for the O2C / RevOps control tower.

Runs the Order-to-Cash control tower and prints the headline: overall status, key
metrics, hard control failures, the top 10 issues, and where outputs were written.
With --compare it runs both the problematic (2026-05) and clean (2026-06) periods
and prints a side-by-side comparison. No API keys, no external services.

    python run_o2c_control_tower.py                  # single period (2026-05)
    python run_o2c_control_tower.py --period 2026-06 # single period
    python run_o2c_control_tower.py --compare        # 2026-05 vs 2026-06
"""

import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.join(HERE, "cfo-office", "o2c")
for _p in (O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_policy as P            # noqa: E402
import o2c_orchestrator as orch   # noqa: E402

COMPARE_PERIODS = ("2026-05", "2026-06")


def _m(x):
    return f"USD {x:,.0f}"


def gather(period, history_run_id=None, history_subdir=None):
    """Run one period and return its comparison row + context."""
    ctx, meta = orch.run(period=period, verbose=False, history_run_id=history_run_id,
                         history_subdir=history_subdir)
    s = ctx.calc["summary"]
    c = ctx.calc["controls_summary"]
    row = {
        "period": period,
        "final_status": meta["final_status"],
        "hard_failures": c["hard_failures"],
        "soft_warnings": c["soft_warnings"],
        "control_pass_rate_pct": c["control_pass_rate_pct"],
        "dso": s["dso"],
        "overdue_ar_usd": s["overdue_ar_usd"],
        "unbilled_revenue_usd": s["unbilled_revenue_usd"],
        "unapplied_cash_usd": s["unapplied_cash_usd"],
        "disputed_ar_usd": s["disputed_ar_usd"],
        "expected_cash_13w_usd": s["expected_cash_13w_usd"],
        "audit_opinion": meta["audit_opinion"],
    }
    return row, ctx, meta


def print_single(period, history_run_id=None):
    # Nest by period too, so the archive layout is identical to --compare:
    # runs/<run_id>/<period>/ and latest/<period>/ (one canonical structure).
    row, ctx, meta = gather(period, history_run_id, history_subdir=period)
    s = ctx.calc["summary"]
    print("=" * 64)
    print(f"  O2C / REVENUE OPERATIONS CONTROL TOWER  |  period {period}")
    print("=" * 64)
    print(f"  STATUS: {meta['final_status']}   (audit opinion: {meta['audit_opinion'].upper()}, "
          f"score {meta['audit_score']}%)")
    print(f"  Control pass rate: {row['control_pass_rate_pct']}%   "
          f"hard failures: {row['hard_failures']}   soft warnings: {row['soft_warnings']}")
    print("\n  METRICS")
    print(f"    Open AR              {_m(s['open_ar_usd'])}")
    print(f"    Overdue AR           {_m(s['overdue_ar_usd'])}")
    print(f"    DSO                  {s['dso']} days (best possible {s['best_possible_dso']})")
    print(f"    Expected cash 13w    {_m(s['expected_cash_13w_usd'])}")
    print(f"    Unbilled / leakage   {_m(s['unbilled_revenue_usd'])}")
    print(f"    Unapplied cash       {_m(s['unapplied_cash_usd'])}")
    print(f"    Disputed AR          {_m(s['disputed_ar_usd'])} ({s['disputed_ar_pct']}%)")
    print(f"    Credit breach        {_m(s['credit_breach_amount_usd'])}")
    print(f"    Bookings->Billings->Revenue->Cash  "
          f"{_m(s['bookings_usd'])} -> {_m(s['billings_usd'])} -> "
          f"{_m(s['recognized_revenue_usd'])} -> {_m(s['cash_collected_usd'])}")

    hard_fail = [r for r in ctx.calc["controls"] if r.severity == "HARD" and r.status == "FAIL"]
    print(f"\n  HARD CONTROL FAILURES ({len(hard_fail)}) - these BLOCK reporting")
    for r in hard_fail:
        print(f"    [FAIL] {r.control_id:32} {r.failing_record_count:>4} items  {_m(r.failing_amount_usd)}")
    if not hard_fail:
        print("    (none - all hard controls pass; reporting can be released)")

    print("\n  TOP 10 ISSUES")
    issues = ctx.escalations()[:10]
    for i, e in enumerate(issues, 1):
        print(f"    {i:>2}. [{e['severity']}] ({e['agent']}) {e['message']}")
    if not issues:
        print("    (none)")

    run_dir = (os.path.join(orch.DEFAULT_OUTPUT_DIR, "runs", history_run_id, period)
               if history_run_id else orch.DEFAULT_OUTPUT_DIR)
    print(f"\n  OUTPUTS written to {run_dir}:")
    for fn in meta["output_files"]:
        print(f"    - {fn}")
    if history_run_id:
        print(f"  latest copy (stable path): {os.path.join(orch.DEFAULT_OUTPUT_DIR, 'latest', period)}")
    print("\n  (deterministic numbers; agents diagnose and narrate but never invent a figure)")


def compare_periods(periods=COMPARE_PERIODS, do_print=True, history_run_id=None):
    """Run each period and return {period: row}; optionally print side by side.

    When history_run_id is given (the scheduled --compare path), each period is
    archived under runs/<run_id>/<period>/ and mirrored to latest/<period>/, so a
    scheduled comparison keeps full per-period history under one run id."""
    rows = {}
    for p in periods:
        row, _ctx, _meta = gather(p, history_run_id=history_run_id, history_subdir=p)
        rows[p] = row
    if do_print:
        labels = [
            ("Final status", "final_status", str),
            ("Hard failures", "hard_failures", str),
            ("Soft warnings", "soft_warnings", str),
            ("Control pass rate", "control_pass_rate_pct", lambda v: f"{v}%"),
            ("DSO (days)", "dso", str),
            ("Overdue AR", "overdue_ar_usd", _m),
            ("Unbilled revenue", "unbilled_revenue_usd", _m),
            ("Unapplied cash", "unapplied_cash_usd", _m),
            ("Disputed AR", "disputed_ar_usd", _m),
            ("Expected cash 13w", "expected_cash_13w_usd", _m),
            ("Audit opinion", "audit_opinion", lambda v: v.upper()),
        ]
        ps = list(periods)
        w = 22
        print("=" * 64)
        print("  O2C CONTROL TOWER - SCENARIO COMPARISON")
        print("=" * 64)
        header = f"  {'Metric':22}" + "".join(f"{p:>{w}}" for p in ps)
        print(header)
        print("  " + "-" * (22 + w * len(ps)))
        for label, key, fmt in labels:
            line = f"  {label:22}" + "".join(f"{fmt(rows[p][key]):>{w}}" for p in ps)
            print(line)
        print("\n  2026-05 is the intentionally problematic period (blocked by hard controls).")
        print("  2026-06 is the clean period: the source data ties out, so it passes - no")
        print("  thresholds were relaxed; only the data differs.")
        if history_run_id:
            print(f"\n  ARCHIVED to {os.path.join(orch.DEFAULT_OUTPUT_DIR, 'runs', history_run_id)}"
                  + os.sep + "<period>/")
            print(f"  latest copy (stable path): {os.path.join(orch.DEFAULT_OUTPUT_DIR, 'latest')}"
                  + os.sep + "<period>/")
    return rows


def _gcs_targets(run_id, output_dir):
    """(local_path, blob_name) for every file under runs/<run_id>/ and latest/.
    The blob name is the path relative to output_dir with forward slashes, so the
    bucket mirrors the local layout: runs/<run_id>/<period>/... and latest/<period>/..."""
    targets = []
    for sub in (os.path.join("runs", run_id), "latest"):
        base = os.path.join(output_dir, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in sorted(files):
                local_path = os.path.join(root, fn)
                blob_name = os.path.relpath(local_path, output_dir).replace(os.sep, "/")
                targets.append((local_path, blob_name))
    return targets


def upload_outputs_to_gcs(bucket_name, run_id, output_dir):
    """Upload this run's outputs (runs/<run_id>/ and latest/) to gs://<bucket>/,
    preserving the path structure. Authentication is Application Default
    Credentials ONLY (no key files, no JSON paths): the Cloud Run Job's service
    account is picked up automatically. Returns the number of files uploaded.
    Raises RuntimeError if any file fails, naming each failure -- a run is not
    successful unless the full evidence reached the bucket."""
    from google.cloud import storage  # lazy: only needed when O2C_OUTPUT_BUCKET is set
    client = storage.Client()          # Application Default Credentials, no args
    bucket = client.bucket(bucket_name)

    targets = _gcs_targets(run_id, output_dir)
    uploaded, failures = 0, []
    for local_path, blob_name in targets:
        try:
            bucket.blob(blob_name).upload_from_filename(local_path)
            uploaded += 1
        except Exception as exc:  # noqa: BLE001 - record which file failed, keep going
            failures.append((blob_name, repr(exc)))

    if failures:
        for blob_name, err in failures:
            print(f"  [GCS UPLOAD FAILED] {blob_name}: {err}", file=sys.stderr)
        raise RuntimeError(
            f"{len(failures)}/{len(targets)} file(s) failed to upload to "
            f"gs://{bucket_name}/ (see errors above)")
    return uploaded


def _maybe_upload(run_id):
    """Upload outputs to GCS when O2C_OUTPUT_BUCKET is set (the Cloud Run Job path).
    When it is not set, do nothing -- local runs stay byte-for-byte as today.
    Exits non-zero if the upload fails totally or partially."""
    bucket_name = os.environ.get("O2C_OUTPUT_BUCKET")
    if not bucket_name:
        return
    try:
        n = upload_outputs_to_gcs(bucket_name, run_id, orch.DEFAULT_OUTPUT_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  GCS upload FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  GCS: uploaded {n} file(s) to gs://{bucket_name}/")
    print(f"  Run: gs://{bucket_name}/runs/{run_id}/")


def main():
    ap = argparse.ArgumentParser(description="Run the O2C / RevOps control tower")
    ap.add_argument("--period", default=P.DEFAULT_PERIOD)
    ap.add_argument("--compare", action="store_true",
                    help="run both 2026-05 and 2026-06 and print a side-by-side comparison")
    args = ap.parse_args()
    # run_id: ONE UTC timestamp per invocation; names the run folder under
    # outputs/runs/ and is recorded (as run_dir) in o2c_audit_trail.json. --compare
    # archives every period under this single run id (runs/<run_id>/<period>/).
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    if args.compare:
        compare_periods(history_run_id=run_id)
    else:
        print_single(args.period, history_run_id=run_id)

    # Cloud Run Job: the container filesystem is ephemeral, so persist the run's
    # outputs to GCS. No-op (local runs unchanged) unless O2C_OUTPUT_BUCKET is set.
    _maybe_upload(run_id)


if __name__ == "__main__":
    main()
