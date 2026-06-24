"""conftest.py - pytest path setup and data guarantee for the O2C test suite."""

import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
AGENTS = os.path.join(O2C, "agents")
for _p in (O2C, AGENTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader  # noqa: E402


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


_ensure_data()
