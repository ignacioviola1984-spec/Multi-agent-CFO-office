"""
run_tests.py - Fallback test runner (no pytest required).

Discovers and runs every test in this folder with unittest. Use when pytest is
not installed:  python cfo-office/o2c/tests/run_tests.py
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
AGENTS = os.path.join(O2C, "agents")
for _p in (O2C, AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader  # noqa: E402


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


if __name__ == "__main__":
    _ensure_data()
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
