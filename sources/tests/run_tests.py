"""
run_tests.py - Fallback test runner for the sources/ layer (no pytest needed).

Discovers and runs every test in this folder with unittest. These tests are
fully deterministic and OFFLINE: they exercise the QuickBooks -> canonical
mapper, the validations, the snapshot writer, the OAuth token store, the
canonical-contract match, and the engine end-to-end, all against a recorded
fixture. No live API call, no secret.

  python sources/tests/run_tests.py
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(TESTS)
for _s in ("canonical", "quickbooks", "snapshots"):
    _p = os.path.join(SRC, _s)
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
