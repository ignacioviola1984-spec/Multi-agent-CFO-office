"""
materialize.py - Source selection + writing canonical CSVs for the engine.

The engine (finance_core) and the MCP surface read ONLY canonical. The active
source is chosen by the SOURCE env var:
    SOURCE=synthetic   -> the existing finance-mcp/data CSVs (default, untouched)
    SOURCE=quickbooks  -> extract sandbox -> snapshot -> validate -> canonical CSVs

For quickbooks, run_quickbooks_pipeline() does the finance-grade path: pull (read
only) -> map to canonical -> validate (deterministic) -> freeze an immutable
snapshot -> materialize the canonical CSVs into _active/. Then point finance_core
at that dir with FINANCE_DATA_DIR (see active_data_dir / sources/README.md).

CLI:
    python sources/canonical/materialize.py --period 2026-05            # uses SOURCE
    python sources/canonical/materialize.py --period 2026-05 --source quickbooks
Exits non-zero if validation fails (so it gates a pipeline).
"""

import argparse
import datetime
import os
import sys

# --- make the sibling source packages importable (flat-import repo style) ---
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # sources/
for _s in ("canonical", "quickbooks", "snapshots"):
    _p = os.path.join(_SRC, _s)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from schema import CONTRACT_TABLES, EXTRA_TABLES
import csvio
import validate as validate_mod
import writer as snapshot_writer
from connector import (SyntheticConnector, QuickBooksConnector, ERPNextConnector,
                       SYNTHETIC_DATA_DIR, load_erpnext)

LIVE_SOURCES = ("quickbooks", "erpnext")     # sources that extract -> validate -> snapshot -> materialize

ALL_COLUMNS = {**CONTRACT_TABLES, **EXTRA_TABLES}
ACTIVE_DIR = os.path.join(_SRC, "canonical", "_active")        # materialized canonical (gitignored)
SNAPSHOT_BASE = os.path.join(_SRC, "snapshots", "data")       # immutable snapshots (gitignored)


def current_source(source=None):
    return (source or os.environ.get("SOURCE", "synthetic")).lower()


def write_canonical_tables(tables, out_dir):
    """Write every canonical table as a CSV with its contract header."""
    for name, cols in ALL_COLUMNS.items():
        csvio.write_table(os.path.join(out_dir, name + ".csv"), cols, tables.get(name, []))
    return out_dir


def active_data_dir(source=None):
    """The directory finance_core should read for the active source."""
    src = current_source(source)
    if src == "synthetic":
        return os.path.abspath(SYNTHETIC_DATA_DIR)
    if src in LIVE_SOURCES:
        if not os.path.exists(os.path.join(ACTIVE_DIR, "pnl_activity.csv")):
            raise RuntimeError(
                f"no materialized {src} canonical yet; run "
                f"`python sources/canonical/materialize.py --period <YYYY-MM> --source {src}` first")
        return os.path.abspath(ACTIVE_DIR)
    raise ValueError(f"unknown SOURCE '{src}' (expected synthetic|{'|'.join(LIVE_SOURCES)})")


def build_connector(source=None):
    src = current_source(source)
    if src == "synthetic":
        return SyntheticConnector()
    if src == "quickbooks":
        from oauth import Config
        from adapter import QuickBooksAdapter
        return QuickBooksConnector(QuickBooksAdapter(Config()))
    if src == "erpnext":
        adapter_mod, _ = load_erpnext()
        return ERPNextConnector(adapter_mod.ERPNextAdapter(adapter_mod.Config()))
    raise ValueError(f"unknown SOURCE '{src}'")


def _snapshot_identity(connector, source):
    """(identity, extra_manifest) for the snapshot path and manifest. The identity
    is the realm (QuickBooks) or the site host (ERPNext)."""
    cfg = getattr(connector, "adapter", None)
    cfg = getattr(cfg, "config", None)
    if source == "erpnext":
        site = getattr(cfg, "site_label", "") or "erpnext"
        return site, {"site_url": getattr(cfg, "base_url", ""),
                      "companies": list(getattr(cfg, "companies", []) or [])}
    realm = getattr(cfg, "realm_id", "") or os.environ.get("QBO_REALM_ID", "sandbox")
    return realm, {}


def run_pipeline(period, source, connector=None, now_iso=None, snapshot_base=SNAPSHOT_BASE,
                 out_dir=ACTIVE_DIR):
    """Extract -> canonical -> validate -> snapshot -> materialize, for any live
    source. Returns a dict. Source-agnostic; QuickBooks and ERPNext both use it."""
    connector = connector or build_connector(source)
    raw = connector.extract_raw(period)
    tables = connector.canonical_tables(period)
    result = validate_mod.validate_canonical(tables, period)
    now_iso = now_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
    identity, extra = _snapshot_identity(connector, source)
    snap_dir, manifest = snapshot_writer.write_snapshot(
        snapshot_base, identity, period, raw, tables, result, now_iso, source=source, extra=extra)
    write_canonical_tables(tables, out_dir)
    return {"snapshot_dir": snap_dir, "data_dir": os.path.abspath(out_dir),
            "validation": result, "manifest": manifest, "tables": tables}


def run_quickbooks_pipeline(period, connector=None, now_iso=None, snapshot_base=SNAPSHOT_BASE,
                            out_dir=ACTIVE_DIR):
    """Back-compat wrapper: the QuickBooks path of the generic pipeline."""
    return run_pipeline(period, "quickbooks", connector=connector, now_iso=now_iso,
                        snapshot_base=snapshot_base, out_dir=out_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Materialize canonical data from the active source")
    ap.add_argument("--period", default="2026-05", help="reporting period YYYY-MM")
    ap.add_argument("--source", default=None,
                    help=f"synthetic|{'|'.join(LIVE_SOURCES)} (default: $SOURCE)")
    ap.add_argument("--print-data-dir", action="store_true",
                    help="just print the active canonical data dir and exit")
    args = ap.parse_args(argv)
    src = current_source(args.source)

    if args.print_data_dir:
        print(active_data_dir(src))
        return 0

    if src == "synthetic":
        print(f"SOURCE=synthetic: engine reads {os.path.abspath(SYNTHETIC_DATA_DIR)} (nothing to do)")
        return 0
    if src not in LIVE_SOURCES:
        raise ValueError(f"unknown SOURCE '{src}' (expected synthetic|{'|'.join(LIVE_SOURCES)})")

    res = run_pipeline(args.period, src, connector=build_connector(src))
    vr = res["validation"]
    print(f"period {args.period} | snapshot {res['snapshot_dir']}")
    print(f"validation: {'PASS' if vr['pass'] else 'FAIL'}")
    for c in vr["checks"]:
        print(f"  [{'ok' if c['ok'] else 'XX'}] {c['name']}: {c['detail']}")
    print(f"canonical materialized to {res['data_dir']}")
    print(f"point the engine at it:  export FINANCE_DATA_DIR={res['data_dir']}")
    return 0 if vr["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
