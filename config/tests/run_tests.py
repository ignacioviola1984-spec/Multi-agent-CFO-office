"""
run_tests.py - Offline test runner for config/ (secrets management).

  python config/tests/run_tests.py

Fully deterministic and offline: no network, no real secret manager, no API key.
"""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TESTS, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
