"""test_o2c_data_integrity.py - datasets exist, schema holds, keys tie out."""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
O2C = os.path.dirname(TESTS)
for _p in (O2C, os.path.join(O2C, "agents")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import o2c_data_loader as loader


def _ensure_data():
    if not os.path.exists(os.path.join(loader.DATA_DIR, "invoices.csv")):
        import generate_data
        generate_data.main()


# id columns that must be unique
PRIMARY_KEYS = {
    "customers": "customer_id", "opportunities": "opportunity_id", "contracts": "contract_id",
    "orders": "order_id", "billing": "billing_schedule_id", "invoices": "invoice_id",
    "credit_memos": "credit_memo_id", "payments": "payment_id", "bank_receipts": "bank_receipt_id",
    "cash_application": "cash_application_id", "revenue": "revenue_schedule_id",
    "disputes": "dispute_id", "collections": "collections_activity_id",
    "credit_limits": "credit_policy_id",
}
# (table, fk column) -> (parent table, parent key). References must NOT dangle.
FOREIGN_KEYS = [
    ("opportunities", "customer_id", "customers", "customer_id"),
    ("contracts", "opportunity_id", "opportunities", "opportunity_id"),
    ("contracts", "customer_id", "customers", "customer_id"),
    ("orders", "contract_id", "contracts", "contract_id"),
    ("billing", "order_id", "orders", "order_id"),
    ("invoices", "order_id", "orders", "order_id"),
    ("invoices", "customer_id", "customers", "customer_id"),
    ("cash_application", "invoice_id", "invoices", "invoice_id"),
    ("revenue", "invoice_id", "invoices", "invoice_id"),
    ("disputes", "invoice_id", "invoices", "invoice_id"),
    ("credit_limits", "customer_id", "customers", "customer_id"),
]


class DataIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_data()
        cls.dfs = loader.load_o2c_data()

    def test_all_15_datasets_exist_and_load(self):
        self.assertEqual(len(self.dfs), 15)
        for key, fname in loader.FILES.items():
            self.assertTrue(os.path.exists(os.path.join(loader.DATA_DIR, fname)),
                            f"missing dataset file {fname}")
            self.assertGreater(len(self.dfs[key]), 0, f"{key} is empty")

    def test_required_columns_present(self):
        for key, cols in loader.EXPECTED_COLUMNS.items():
            have = set(self.dfs[key].columns)
            missing = [c for c in cols if c not in have]
            self.assertEqual(missing, [], f"{key} missing columns {missing}")

    def test_primary_keys_unique(self):
        for key, pk in PRIMARY_KEYS.items():
            n_dup = int(self.dfs[key][pk].duplicated().sum())
            self.assertEqual(n_dup, 0, f"{key}.{pk} has {n_dup} duplicate keys")

    def test_foreign_keys_do_not_dangle(self):
        # Forward-missing links (a closed-won opp with no contract) are SEEDED
        # exceptions and are fine; what must never happen is a dangling reference.
        for child, fk, parent, pkey in FOREIGN_KEYS:
            child_ids = set(self.dfs[child][fk].dropna().astype(str))
            child_ids.discard("")
            parent_ids = set(self.dfs[parent][pkey].astype(str))
            dangling = child_ids - parent_ids
            self.assertEqual(dangling, set(),
                             f"{child}.{fk} has dangling references: {list(dangling)[:5]}")

    def test_currency_normalization_present(self):
        self.assertIn("invoice_amount_usd", self.dfs["invoices"].columns)
        self.assertIn("credit_limit_usd", self.dfs["customers"].columns)
        # USD invoices: usd amount equals local amount
        usd = self.dfs["invoices"][self.dfs["invoices"]["currency"] == "USD"]
        self.assertTrue((abs(usd["invoice_amount_usd"] - usd["invoice_amount"]) < 0.01).all())


if __name__ == "__main__":
    unittest.main(verbosity=2)
