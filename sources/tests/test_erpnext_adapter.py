"""test_erpnext_adapter.py - the ERPNext adapter is READ-ONLY (GET only, no write
method) and its period extraction assembles the full raw shape the mapper needs.
Offline: an injected transport stands in for the live Frappe API."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext  # noqa: F401  (sets up sys.path for canonical/snapshots)
import connector

adapter_mod, _ = connector.load_erpnext()

EXPECTED_KEYS = {
    "companies", "fx_rates", "accounts", "reports", "sales_invoices", "purchase_invoices",
    "payment_entries", "sales_orders", "quotations", "opportunities", "dunnings",
    "payment_requests", "customers", "suppliers", "gl_entries", "bank_accounts",
}


class _Cfg:
    base_url = "https://demo.frappe.cloud"
    api_key, api_secret = "k", "s"
    companies = []
    site_label = "demo.frappe.cloud"

    def auth_header(self):
        return {"Authorization": "token k:s"}


class ErpNextAdapterTest(unittest.TestCase):
    def setUp(self):
        self.methods = []

        def transport(method, url, headers, timeout=30):
            self.methods.append(method)
            if "query_report.run" in url:
                return 200, {}, {"message": {"result": []}}
            if "/api/resource/Company" in url:
                return 200, {}, {"data": [{"name": "Co", "company_name": "Co", "abbr": "CO",
                                           "default_currency": "USD", "country": "X"}]}
            return 200, {}, {"data": []}

        self.adapter = adapter_mod.ERPNextAdapter(config=_Cfg(), transport=transport, sleep=lambda *_: None)

    def test_extract_raw_assembles_full_shape_with_only_GET(self):
        raw = self.adapter.extract_raw("2026-05")
        self.assertEqual(set(raw), EXPECTED_KEYS)
        self.assertIn("Co", raw["reports"])               # reports keyed by company
        self.assertTrue(self.methods)                      # it called the API
        self.assertEqual(set(self.methods), {"GET"})       # read-only: GET only

    def test_no_write_method_exists_on_the_adapter(self):
        bad = [m for m in dir(adapter_mod.ERPNextAdapter)
               if any(w in m.lower() for w in ("create", "update", "delete", "post",
                                               "put", "insert", "submit", "save"))]
        self.assertEqual(bad, [], f"adapter exposes write-ish methods: {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
