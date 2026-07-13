"""_util.py - shared offline helpers for the AP Control Tower adapter tests.

Flat-import style (like the rest of sources/): adds sources/ap_control_tower and
sources/canonical to sys.path so `import adapter`, `import contract`,
`import schema`, `from connector import SyntheticConnector` resolve.
"""

import copy
import os
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(_TESTS)
SRC = os.path.dirname(PKG)
REPO = os.path.dirname(SRC)
for _p in (PKG, os.path.join(SRC, "canonical")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import adapter  # noqa: E402

FIXTURE = os.path.join(PKG, "fixtures", "approved_payment_proposal.csv")
ENTITY_MAP = os.path.join(PKG, "fixtures", "entity_map.json")
PERIOD = "2026-05"


def base_canonical():
    """The existing synthetic canonical set (finance-mcp/data), read unchanged."""
    from connector import SyntheticConnector
    return SyntheticConnector().canonical_tables()


def entity_map():
    return adapter.EntityMap.from_json_file(ENTITY_MAP)


def read_fixture():
    """(columns, rows) of the synthetic approved-proposal export."""
    return adapter.read_export(FIXTURE)


def valid_rows():
    """A fresh, mutable copy of the fixture rows (for negative-case mutation)."""
    _cols, rows = read_fixture()
    return copy.deepcopy(rows)


def columns():
    cols, _rows = read_fixture()
    return list(cols)
