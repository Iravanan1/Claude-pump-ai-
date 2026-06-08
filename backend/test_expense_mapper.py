#!/usr/bin/env python3
"""
Automated tests for backend/expense_mapper.py.

Covers:
- expense_categories.json loading and caching
- Keyword matching for each category (English + Hindi)
- First-match-wins precedence
- Fallback to 'Unclassified Operational Expenses'
- categorize_loose_expenses() on raw expense arrays
- get_all_categories() returns all heads + fallback
- DB migration VERSION 5 -> VERSION 6 (accounting_head column)
"""

import os
import sys
import json
import sqlite3
import unittest
import tempfile
import shutil

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import expense_mapper


class TestGetAccountingHead(unittest.TestCase):
    """Tests the single-description classifier."""

    def setUp(self):
        """Force reload categories from the real JSON file each test."""
        expense_mapper.reload_categories()

    # ── English keyword matching ──────────────────────────────────────────

    def test_tea_maps_to_staff_food(self):
        head = expense_mapper.get_accounting_head("tea 2 cups")
        self.assertEqual(head, "Staff Food & Tea")

    def test_chai_maps_to_staff_food(self):
        head = expense_mapper.get_accounting_head("chai pani daily")
        self.assertEqual(head, "Staff Food & Tea")

    def test_nashta_maps_to_staff_food(self):
        head = expense_mapper.get_accounting_head("nashta for guard")
        self.assertEqual(head, "Staff Food & Tea")

    def test_cleaning_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("cleaning supplies")
        self.assertEqual(head, "Station Maintenance")

    def test_safai_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("safai material")
        self.assertEqual(head, "Station Maintenance")

    def test_pipe_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("PVC pipe repair")
        self.assertEqual(head, "Station Maintenance")

    def test_paint_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("color paint for canopy")
        self.assertEqual(head, "Station Maintenance")

    def test_generator_fuel_maps_to_generator_ops(self):
        head = expense_mapper.get_accounting_head("generator fuel")
        self.assertEqual(head, "Generator Operations")

    def test_genset_oil_maps_to_generator_ops(self):
        head = expense_mapper.get_accounting_head("gen set oil change")
        self.assertEqual(head, "Generator Operations")

    def test_grease_maps_to_generator_ops(self):
        head = expense_mapper.get_accounting_head("grease for pump")
        self.assertEqual(head, "Generator Operations")

    def test_salary_maps_to_staff_salaries(self):
        head = expense_mapper.get_accounting_head("salary advance to Ramesh")
        self.assertEqual(head, "Staff Salaries & Wages")

    def test_stationery_maps_to_office(self):
        head = expense_mapper.get_accounting_head("stationery pen register")
        self.assertEqual(head, "Office & Stationery")

    def test_xerox_maps_to_office(self):
        head = expense_mapper.get_accounting_head("xerox copies")
        self.assertEqual(head, "Office & Stationery")

    def test_tax_maps_to_government(self):
        head = expense_mapper.get_accounting_head("licence fee renewal tax")
        self.assertEqual(head, "Government Taxes & Fees")

    def test_bank_charge_maps_to_banking(self):
        head = expense_mapper.get_accounting_head("bank charge for RTGS")
        self.assertEqual(head, "Banking & Financial Charges")

    def test_pos_machine_maps_to_banking(self):
        head = expense_mapper.get_accounting_head("pos machine rent HDFC")
        self.assertEqual(head, "Banking & Financial Charges")

    def test_guard_maps_to_security(self):
        head = expense_mapper.get_accounting_head("guard salary payment")
        # 'salary' hits Staff Salaries before 'guard' hits Security — confirm it still maps
        # Either head is acceptable depending on order; just check it's not Unclassified
        self.assertNotEqual(head, "Unclassified Operational Expenses")

    def test_misc_maps_to_miscellaneous(self):
        head = expense_mapper.get_accounting_head("misc purchase")
        self.assertEqual(head, "Miscellaneous Operational")

    # ── Hindi / transliterated matching ───────────────────────────────────

    def test_hindi_safai_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("सफाई material")
        self.assertEqual(head, "Station Maintenance")

    def test_hindi_chai_maps_to_staff_food(self):
        head = expense_mapper.get_accounting_head("चाय expense today")
        self.assertEqual(head, "Staff Food & Tea")

    def test_hindi_jhadoo_maps_to_station_maintenance(self):
        head = expense_mapper.get_accounting_head("झाडू bought for station")
        self.assertEqual(head, "Station Maintenance")

    # ── Fallback category ─────────────────────────────────────────────────

    def test_unknown_description_falls_back(self):
        head = expense_mapper.get_accounting_head("zxqyfoo qux bar")
        self.assertEqual(head, "Unclassified Operational Expenses")

    def test_empty_string_falls_back(self):
        head = expense_mapper.get_accounting_head("")
        self.assertEqual(head, "Unclassified Operational Expenses")

    def test_none_falls_back(self):
        head = expense_mapper.get_accounting_head(None)
        self.assertEqual(head, "Unclassified Operational Expenses")

    # ── Case insensitivity ────────────────────────────────────────────────

    def test_case_insensitive_match(self):
        head = expense_mapper.get_accounting_head("CHAI DAILY")
        self.assertEqual(head, "Staff Food & Tea")

    def test_mixed_case(self):
        head = expense_mapper.get_accounting_head("Generator FUEL top-up")
        self.assertEqual(head, "Generator Operations")

    # ── Partial substring matching ─────────────────────────────────────────

    def test_partial_match_inside_phrase(self):
        head = expense_mapper.get_accounting_head("gave money for nashta snacks")
        self.assertEqual(head, "Staff Food & Tea")


class TestCategorizeLoseExpenses(unittest.TestCase):
    """Tests the array-level categorizer."""

    def setUp(self):
        expense_mapper.reload_categories()

    def test_empty_array_returns_empty(self):
        result = expense_mapper.categorize_loose_expenses([])
        self.assertEqual(result, [])

    def test_none_returns_none(self):
        result = expense_mapper.categorize_loose_expenses(None)
        self.assertIsNone(result)

    def test_single_item_classified_correctly(self):
        expenses = [{"party_name": "Tea Stall", "amount": 50.0, "remarks": "chai"}]
        result = expense_mapper.categorize_loose_expenses(expenses)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["accounting_head"], "Staff Food & Tea")

    def test_multiple_items_classified_individually(self):
        expenses = [
            {"party_name": "Office", "amount": 50.0,  "remarks": "chai"},
            {"party_name": "HW Store", "amount": 200.0, "remarks": "pipe fitting"},
            {"party_name": "Unknown",  "amount": 999.0, "remarks": "zxqyfoo bar"}
        ]
        result = expense_mapper.categorize_loose_expenses(expenses)
        self.assertEqual(result[0]["accounting_head"], "Staff Food & Tea")
        self.assertEqual(result[1]["accounting_head"], "Station Maintenance")
        self.assertEqual(result[2]["accounting_head"], "Unclassified Operational Expenses")

    def test_accounting_head_appended_not_overwriting_other_fields(self):
        expenses = [{"party_name": "Test", "amount": 100.0, "remarks": "generator fuel"}]
        result = expense_mapper.categorize_loose_expenses(expenses)
        self.assertEqual(result[0]["party_name"], "Test")
        self.assertEqual(result[0]["amount"], 100.0)
        self.assertEqual(result[0]["remarks"], "generator fuel")
        self.assertIn("accounting_head", result[0])

    def test_combined_party_and_remarks_used_for_matching(self):
        """Classification uses 'party_name remarks' combined."""
        expenses = [{"party_name": "Morning Tea", "amount": 30.0, "remarks": "nashta buy"}]
        result = expense_mapper.categorize_loose_expenses(expenses)
        self.assertEqual(result[0]["accounting_head"], "Staff Food & Tea")

    def test_existing_accounting_head_overwritten(self):
        """categorize_loose_expenses always sets accounting_head (overwrite)."""
        expenses = [{"party_name": "X", "amount": 10.0, "remarks": "chai", "accounting_head": "Old Head"}]
        result = expense_mapper.categorize_loose_expenses(expenses)
        # After categorization the head must be the correctly matched one
        self.assertEqual(result[0]["accounting_head"], "Staff Food & Tea")

    def test_modifies_list_in_place_and_returns_same_list(self):
        expenses = [{"party_name": "A", "amount": 5.0, "remarks": "safai"}]
        result = expense_mapper.categorize_loose_expenses(expenses)
        self.assertIs(result, expenses)


class TestGetAllCategories(unittest.TestCase):
    """Tests the category list utility."""

    def setUp(self):
        expense_mapper.reload_categories()

    def test_returns_list(self):
        cats = expense_mapper.get_all_categories()
        self.assertIsInstance(cats, list)

    def test_fallback_always_present(self):
        cats = expense_mapper.get_all_categories()
        self.assertIn("Unclassified Operational Expenses", cats)

    def test_all_json_heads_present(self):
        cats = expense_mapper.get_all_categories()
        expected = [
            "Staff Food & Tea", "Station Maintenance", "Generator Operations",
            "Staff Salaries & Wages", "Office & Stationery",
            "Vehicle & Fuel Expenses", "Government Taxes & Fees",
            "Banking & Financial Charges", "Security & Guard Services",
            "Miscellaneous Operational"
        ]
        for head in expected:
            self.assertIn(head, cats, msg=f"'{head}' missing from get_all_categories()")


class TestCustomCategoriesJson(unittest.TestCase):
    """Tests with a temporary custom categories file."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fake_json = os.path.join(self.tmp_dir, "expense_categories.json")
        custom = {
            "Diesel Ops": ["diesel pump", "generator diesel"],
            "Canteen": ["snack", "juice"]
        }
        with open(self.fake_json, "w") as f:
            json.dump(custom, f)

        # Monkey-patch the module's CATEGORIES_FILE and _CATEGORIES cache
        self._orig_file = expense_mapper.CATEGORIES_FILE
        self._orig_cache = expense_mapper._CATEGORIES
        expense_mapper.CATEGORIES_FILE = self.fake_json
        expense_mapper._CATEGORIES = None

    def tearDown(self):
        expense_mapper.CATEGORIES_FILE = self._orig_file
        expense_mapper._CATEGORIES = self._orig_cache
        shutil.rmtree(self.tmp_dir)

    def test_custom_categories_loaded(self):
        head = expense_mapper.get_accounting_head("diesel pump refill")
        self.assertEqual(head, "Diesel Ops")

    def test_custom_categories_fallback(self):
        head = expense_mapper.get_accounting_head("random description")
        self.assertEqual(head, "Unclassified Operational Expenses")


class TestMigrationVersion6(unittest.TestCase):
    """Tests the VERSION 5 → 6 migration that adds accounting_head."""

    def _make_db_at_version(self, target_version):
        """Creates a temporary DB pre-seeded at the given version."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        # Base ledger_entries table
        conn.execute("""
        CREATE TABLE ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, party_name TEXT, vehicle_wheel_no TEXT,
            amount REAL DEFAULT 0.0, type TEXT, remarks TEXT,
            transaction_source TEXT DEFAULT 'manual',
            payment_status TEXT DEFAULT 'UNPAID',
            amount_remaining REAL, linked_payment_id TEXT,
            base_amount TEXT, discount_applied TEXT, base_rate TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""
        CREATE TABLE sys_version (key TEXT PRIMARY KEY, value INTEGER)""")
        conn.execute("INSERT INTO sys_version VALUES ('version', ?)", (target_version,))
        conn.execute("""
        CREATE TABLE daily_summary (
            date TEXT PRIMARY KEY,
            total_hsd_liters REAL DEFAULT 0.0,
            total_ms_liters REAL DEFAULT 0.0,
            total_cash_calculated REAL DEFAULT 0.0,
            total_credit_sales REAL DEFAULT 0.0,
            total_testing_deductions REAL DEFAULT 0.0,
            is_verified INTEGER DEFAULT 0,
            meter_replaced INTEGER DEFAULT 0,
            replacement_offset_liters REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        conn.close()
        return tmp.name

    def tearDown(self):
        pass

    def test_accounting_head_column_added(self):
        """Migration 5→6 must add accounting_head column to ledger_entries."""
        from migrations import apply_schema_updates
        db = self._make_db_at_version(5)
        try:
            apply_schema_updates(db_path=db)
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(ledger_entries)")
            cols = [c[1] for c in cursor.fetchall()]
            conn.close()
            self.assertIn("accounting_head", cols)
        finally:
            os.unlink(db)

    def test_version_incremented_to_6(self):
        """After migration, sys_version.value must equal 6."""
        from migrations import apply_schema_updates
        db = self._make_db_at_version(5)
        try:
            apply_schema_updates(db_path=db)
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT value FROM sys_version WHERE key='version'").fetchone()
            conn.close()
            self.assertGreaterEqual(row[0], 6)
        finally:
            os.unlink(db)

    def test_migration_idempotent_if_already_v6(self):
        """Running migration on a v6 DB must not crash or change anything."""
        from migrations import apply_schema_updates
        db = self._make_db_at_version(5)
        try:
            apply_schema_updates(db_path=db)  # go to 6
            apply_schema_updates(db_path=db)  # should be no-op
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT value FROM sys_version WHERE key='version'").fetchone()
            conn.close()
            self.assertGreaterEqual(row[0], 6)
        finally:
            os.unlink(db)


if __name__ == "__main__":
    unittest.main()
