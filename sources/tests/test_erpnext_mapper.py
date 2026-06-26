"""test_erpnext_mapper.py - the ERPNext -> canonical mapper is deterministic,
multi-company / multi-currency, and never leaks a vendor object into the engine
tables. Offline against the recorded fixture."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture_erpnext
from _fixture_erpnext import build_canonical, PERIOD
from schema import (CONTRACT_TABLES, O2C_TABLES, BS_ASSET_CODES, BS_LIAB_CODES,
                    BS_EQUITY_CODES, BS_AR, OPEN)


def _sum(rows, codes, eid=None):
    return round(sum(float(r["amount_local"]) for r in rows
                     if r["account_code"] in codes and (eid is None or r["entity_id"] == eid)), 2)


class ErpNextMapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_canonical()

    def test_two_entities_with_their_currencies(self):
        ents = {e["entity_id"]: e for e in self.c["entities"]}
        self.assertEqual(set(ents), {"LUS", "LUK"})
        self.assertEqual(ents["LUS"]["currency"], "USD")
        self.assertEqual(ents["LUK"]["currency"], "GBP")

    def test_fx_rates_present_for_every_entity_currency(self):
        fx = {r["currency"]: r["units_per_usd"] for r in self.c["fx_rates"]
              if r["period"] == PERIOD}
        self.assertEqual(fx.get("USD"), "1")
        self.assertEqual(float(fx.get("GBP")), 0.80)

    def test_pnl_rolls_up_per_entity_into_canonical_codes(self):
        pnl = self.c["pnl_activity"]
        # US revenue 100000, UK revenue 80000 (document currency, pre-consolidation)
        self.assertEqual(_sum(pnl, ("4000",), "LUS"), 100000.0)
        self.assertEqual(_sum(pnl, ("4000",), "LUK"), 80000.0)
        # every code is one of the 12 canonical codes
        valid = {a["account_code"] for a in self.c["chart_of_accounts"]}
        self.assertTrue(all(r["account_code"] in valid for r in pnl))

    def test_balance_sheet_foots_per_entity(self):
        bs = self.c["balance_sheet"]
        for eid in ("LUS", "LUK"):
            a = _sum(bs, BS_ASSET_CODES, eid)
            l = _sum(bs, BS_LIAB_CODES, eid)
            q = _sum(bs, BS_EQUITY_CODES, eid)
            self.assertAlmostEqual(a, l + q, places=2, msg=f"{eid} does not foot")

    def test_ar_invoices_tie_to_control_per_entity_and_exclude_returns(self):
        for eid, expect in (("LUS", 50000.0), ("LUK", 40000.0)):
            open_ar = round(sum(float(r["amount_local"]) for r in self.c["ar_invoices"]
                                if r["entity_id"] == eid and r["status"] == OPEN), 2)
            self.assertEqual(open_ar, expect)
            self.assertEqual(open_ar, _sum(self.c["balance_sheet"], (BS_AR,), eid))
        # the is_return=1 invoice is a credit note, not an AR invoice
        self.assertTrue(all("CN" not in r["invoice_id"] for r in self.c["ar_invoices"]))
        self.assertEqual(len(self.c["credit_notes"]), 1)

    def test_trial_balance_balances_per_entity(self):
        for eid in ("LUS", "LUK"):
            e = [r for r in self.c["trial_balance"] if r["entity_id"] == eid]
            d = round(sum(float(r["debit"]) for r in e), 2)
            c = round(sum(float(r["credit"]) for r in e), 2)
            self.assertAlmostEqual(d, c, places=2, msg=f"{eid} TB unbalanced")

    def test_o2c_tables_are_populated(self):
        for t in O2C_TABLES:
            self.assertGreater(len(self.c[t]), 0, f"{t} empty")
        # entity ids on O2C rows are canonical (company abbrs), not ERPNext company names
        self.assertTrue(all(r["entity_id"] in ("LUS", "LUK") for r in self.c["sales_orders"]))

    def test_no_erpnext_object_names_leak_into_engine_tables(self):
        # engine contract tables must carry only canonical fields, no Frappe doctype noise
        for name, cols in CONTRACT_TABLES.items():
            for row in self.c.get(name, []):
                self.assertEqual(set(row) - set(cols), set(), f"{name} has extra keys")


if __name__ == "__main__":
    unittest.main(verbosity=2)
