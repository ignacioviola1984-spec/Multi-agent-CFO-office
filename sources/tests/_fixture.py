"""Shared test helpers: sys.path setup + fixture loading (offline)."""

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

FIXTURE_PATH = os.path.join(SRC, "fixtures", "quickbooks_sandbox", "sandbox_extract_2026_05.json")
PERIOD = "2026-05"
ENTITY_ID = "US"
ENTITY_NAME = "QuickBooks Sandbox Co."


def load_raw():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_canonical():
    import mapper
    return mapper.build_canonical(load_raw(), ENTITY_ID, ENTITY_NAME, PERIOD)
