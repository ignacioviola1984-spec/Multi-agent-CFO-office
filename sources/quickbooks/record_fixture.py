"""
record_fixture.py - Capture ONE real sandbox extraction as the test fixture.

The committed fixture (sources/fixtures/quickbooks_sandbox/sandbox_extract_*.json)
is REPRESENTATIVE: it is modeled on Intuit's documented sandbox response shapes so
the mapper and validations can be developed and tested with no live API and no
secret. Run this once, against your own sandbox realm, to replace it with a
genuine capture and harden the tests on real data:

    # with QBO_CLIENT_ID / QBO_CLIENT_SECRET / QBO_REFRESH_TOKEN / QBO_REALM_ID in .env
    python sources/quickbooks/record_fixture.py --period 2026-05

It writes the combined raw responses (read-only GETs only) to the fixture path.
Nothing here writes to QuickBooks.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
for _s in ("canonical", "quickbooks", "snapshots"):
    _p = os.path.join(SRC, _s)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from oauth import Config
from adapter import QuickBooksAdapter
from connector import QuickBooksConnector

FIXTURE_DIR = os.path.join(SRC, "fixtures", "quickbooks_sandbox")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Record a real sandbox extraction as the test fixture")
    ap.add_argument("--period", default="2026-05", help="reporting period YYYY-MM")
    ap.add_argument("--out", default=None, help="output path (default: the committed fixture)")
    args = ap.parse_args(argv)

    config = Config()
    config.require_app_credentials()
    conn = QuickBooksConnector(QuickBooksAdapter(config))
    raw = conn.extract_raw(args.period)

    out = args.out or os.path.join(FIXTURE_DIR, f"sandbox_extract_{args.period.replace('-', '_')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, sort_keys=True)
    counts = {k: (len(v) if isinstance(v, list) else "report") for k, v in raw.items()}
    print(f"recorded real sandbox extraction for {args.period} -> {out}")
    print(f"record counts: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
