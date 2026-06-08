#!/usr/bin/env python3
"""
Comprehensive Unit Tests for cash_denominations.py.
Covers SQLite table setups, notes multiplication aggregation, expected book cash queries,
and mismatch delta calculations (positive/negative overage/shortage bounds).
"""

import os
import sqlite3
import tempfile
import unittest

# Make sure imports are resolved from current directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cash_denominations import (
    init_cash_denominations_db,
    verify_cash_vault_balance,
    get_cash_denomination
)

def _fresh_db() -> str:
    """Helper to initialize a temporary database for testing."""
    tmp = tempfile.mktemp(suffix=".db")
    init_cash_denominations_db(tmp)
    # Also initialize mock daily_summary and daily_ledger tables
    conn = sqlite3.connect(tmp)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_summary (
        date TEXT PRIMARY KEY,
        total_hsd_liters REAL DEFAULT 0.0,
        total_ms_liters REAL DEFAULT 0.0,
        total_cash_calculated REAL DEFAULT 0.0,
        total_credit_sales REAL DEFAULT 0.0,
        total_testing_deductions REAL DEFAULT 0.0,
        is_verified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE,
        total_sales_liters REAL,
        total_amount_inr REAL,
        cash_tender REAL,
        upi_tender REAL,
        paytm_transfers REAL,
        card_tender REAL,
        udhaar_sales REAL,
        expenses_amount REAL,
        validation_status TEXT,
        raw_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    return tmp

class TestCashDenominationsSchema(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def test_schema_table_exists(self):
        conn = sqlite3.connect(self.db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cash_denominations'")
        row = cursor.fetchone()
        self.assertIsNotNone(row, "cash_denominations table should exist")
        
        cursor.execute("PRAGMA table_info(cash_denominations)")
        cols = {col[1]: col[2] for col in cursor.fetchall()}
        self.assertIn("date", cols)
        self.assertIn("notes_500", cols)
        self.assertIn("notes_200", cols)
        self.assertIn("notes_100", cols)
        self.assertIn("notes_50", cols)
        self.assertIn("notes_20", cols)
        self.assertIn("notes_10", cols)
        self.assertIn("coins_total", cols)
        self.assertIn("calculated_physical_sum", cols)
        self.assertIn("mismatch_vs_book_sales", cols)
        conn.close()

    def test_idempotent_init(self):
        init_cash_denominations_db(self.db)
        init_cash_denominations_db(self.db)

class TestDenominationCalculation(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def _seed_daily_data(self, date_str, calc_sales, upi=0.0, paytm=0.0, card=0.0, udhaar=0.0):
        conn = sqlite3.connect(self.db)
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO daily_summary (date, total_cash_calculated, total_credit_sales)
        VALUES (?, ?, ?)
        """, (date_str, calc_sales, udhaar))
        
        cursor.execute("""
        INSERT INTO daily_ledger (date, total_amount_inr, upi_tender, paytm_transfers, card_tender, udhaar_sales)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (date_str, calc_sales, upi, paytm, card, udhaar))
        conn.commit()
        conn.close()

    def test_notes_multiplication_aggregation(self):
        """Should sum multipliers correctly: notes_500 * 500, etc."""
        self._seed_daily_data("2026-06-01", 1000.0) # Expected cash is 1000
        
        note_counts = {
            "notes_500": 1,   # 500
            "notes_200": 2,   # 400
            "notes_100": 0,
            "notes_50": 1,    # 50
            "notes_20": 2,    # 40
            "notes_10": 1,    # 10
            "coins_total": 5.5 # 5.5
        }
        
        report = verify_cash_vault_balance("2026-06-01", note_counts, db_path=self.db)
        # Sum = 500 + 400 + 50 + 40 + 10 + 5.5 = 1005.50
        self.assertAlmostEqual(report["calculated_physical_sum"], 1005.50)

    def test_reconciliation_net_expected_cash(self):
        """Expected Net Cash = Total calculated - upi - paytm - card - udhaar."""
        self._seed_daily_data(
            date_str="2026-06-01",
            calc_sales=50000.00,
            upi=12000.0,
            paytm=3000.0,
            card=5000.0,
            udhaar=10000.0
        )
        
        # Expected = 50000 - 12000 - 3000 - 5000 - 10000 = 20000.0
        report = verify_cash_vault_balance("2026-06-01", {}, db_path=self.db)
        self.assertAlmostEqual(report["expected_book_sales"], 20000.0)

    def test_mismatch_delta_overage(self):
        """ Overage: calculated physical sum > expected book cash """
        self._seed_daily_data("2026-06-01", 10000.0, upi=4000.0) # Expected cash = 6000
        
        note_counts = {
            "notes_500": 13, # 6500.0
        }
        report = verify_cash_vault_balance("2026-06-01", note_counts, db_path=self.db)
        self.assertAlmostEqual(report["mismatch_vs_book_sales"], 500.0)

    def test_mismatch_delta_shortage(self):
        """ Shortage: calculated physical sum < expected book cash """
        self._seed_daily_data("2026-06-01", 10000.0, upi=4000.0) # Expected cash = 6000
        
        note_counts = {
            "notes_500": 11, # 5500.0
        }
        report = verify_cash_vault_balance("2026-06-01", note_counts, db_path=self.db)
        self.assertAlmostEqual(report["mismatch_vs_book_sales"], -500.0)

    def test_get_cash_denomination_empty_default(self):
        """Querying an unsaved date should return zero-filled dictionary safely."""
        data = get_cash_denomination("2020-01-01", db_path=self.db)
        self.assertEqual(data["notes_500"], 0)
        self.assertEqual(data["notes_200"], 0)
        self.assertAlmostEqual(data["coins_total"], 0.0)
        self.assertAlmostEqual(data["calculated_physical_sum"], 0.0)

    def test_get_cash_denomination_saved_retrieval(self):
        """Querying a saved date should yield correct values."""
        note_counts = {
            "notes_500": 2,
            "notes_100": 3,
            "coins_total": 45.50
        }
        verify_cash_vault_balance("2026-06-01", note_counts, db_path=self.db)
        
        data = get_cash_denomination("2026-06-01", db_path=self.db)
        self.assertEqual(data["notes_500"], 2)
        self.assertEqual(data["notes_100"], 3)
        self.assertAlmostEqual(data["coins_total"], 45.50)
        self.assertAlmostEqual(data["calculated_physical_sum"], 1345.50)

if __name__ == "__main__":
    unittest.main()
