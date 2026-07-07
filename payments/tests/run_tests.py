"""
run_tests.py - Offline test runner for payments/ (the governed write path).

  python payments/tests/run_tests.py

Deterministic and offline: SandboxRail (local ledger), LocalDevIdentity approvals,
canonical balances from a temp directory. No network, no real rail, no API key.
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TESTS, "..", ".."))
PKG = os.path.join(ROOT, "payments")
for _p in (ROOT, PKG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
