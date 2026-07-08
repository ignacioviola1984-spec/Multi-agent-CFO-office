"""Headless smoke test: run every station via Streamlit AppTest and report any
uncaught exception. Also exercises the close-station buttons and the O2C/ERP
toggles. Run: python cfo-demo-v2/_verify_app.py"""

import os
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
NAV = [
    "🏠  Overview",
    "1 · ERP - data in",
    "2 · O2C control tower",
    "3 · Month-end close",
    "4 · Evals - does it hold?",
    "5 · Self-improvement",
]


def check(label, at):
    errs = [f"{type(e.value).__name__}: {e.value}" for e in at.exception]
    print(f"  {'FAIL' if errs else 'ok  '}  {label}")
    for e in errs:
        print(f"        -> {e}")
    return not errs


ok = True
for nav in NAV:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value(nav).run()
    ok &= check(nav, at)

# ERP: exercise each tamper option + the synthetic source toggle
at = AppTest.from_file(APP, default_timeout=60).run()
at.sidebar.radio[0].set_value(NAV[1]).run()
at.radio(key="erp_source").set_value("Synthetic (Lumen)").run()
ok &= check("ERP · synthetic source", at)
for opt in [r["label"] for r in __import__("json").load(open(
        os.path.join(os.path.dirname(APP), "snapshots", "sources.json"), encoding="utf-8"))["tampers"]]:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value(NAV[1]).run()
    at.radio(key="erp_tamper").set_value(opt).run()
    ok &= check(f"ERP · tamper: {opt[:32]}", at)

# O2C: clean month toggle
at = AppTest.from_file(APP, default_timeout=60).run()
at.sidebar.radio[0].set_value(NAV[2]).run()
at.radio(key="o2c_period").set_value("🟢 Clean month (2026-06)").run()
ok &= check("O2C · clean month", at)

# Close: start it, approve every first-line gate, then the CFO's final one
GATES = ["Controller", "Treasury", "Accounts Receivable", "Accounts Payable", "Tax",
         "Accounting & Close", "Financial Reporting", "FP&A", "Strategic Finance",
         "Internal Controls", "Audit"]
at = AppTest.from_file(APP, default_timeout=120).run()
at.sidebar.radio[0].set_value(NAV[3]).run()
at.button(key="start_close").click().run()                   # Start the close
ok &= check("Close · after start (gate 1 pending)", at)
for fn in GATES:
    at.button(key=f"ap_{fn}").click().run()                  # approve each gate
    ok &= check(f"Close · approved: {fn}", at)
at.button(key="cfo_ap").click().run()                        # CFO final sign-off
ok &= check("Close · after CFO sign-off (board pack)", at)

print("\nRESULT:", "ALL STATIONS OK" if ok else "FAILURES ABOVE")
