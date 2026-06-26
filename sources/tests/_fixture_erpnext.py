"""Shared ERPNext test helpers: sys.path setup + fixture loading (offline).

The ERPNext vendor modules are loaded via connector.load_erpnext() (by path,
under unique names) so they never collide with the QuickBooks adapter/mapper."""

import json
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(TESTS)
REPO = os.path.dirname(SRC)
for _s in ("canonical", "quickbooks", "snapshots"):
    _p = os.path.join(SRC, _s)
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIXTURE_PATH = os.path.join(SRC, "fixtures", "erpnext_demo", "erpnext_extract_2026_05.json")
PERIOD = "2026-05"


def load_raw():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_canonical():
    import connector
    _, mapper = connector.load_erpnext()
    return mapper.build_canonical(load_raw(), PERIOD)
