"""
run_tests.py - offline, deterministic runner for the AP Control Tower adapter
(no pytest needed, same pattern as sources/tests/run_tests.py).

Discovers and runs every test in this folder with unittest. All tests are fully
OFFLINE and deterministic: the contract, the fail-closed validations, the mapping
to canonical ap_invoices, the manifest/trace evidence, and the end-to-end
integration with finance_core all run against the synthetic fixture and the
synthetic canonical. No live API, no secret, no network.

  python sources/ap_control_tower/tests/run_tests.py
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(TESTS)
SRC = os.path.dirname(PKG)
for _p in (TESTS, PKG, os.path.join(SRC, "canonical")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
