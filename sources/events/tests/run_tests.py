"""
run_tests.py - Offline test runner for sources/events/ (webhook ingestion).

  python sources/events/tests/run_tests.py

Deterministic and offline: the receiver core is exercised directly (no HTTP
server, no network), signatures use a test HMAC secret, and replay is compared by
sha256. No API key.
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TESTS, "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
