"""
Comprehensive unit tests for local_analytics.py.
"""

import os
import sqlite3
import tempfile
import unittest
import shutil

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from local_analytics import generate_all_charts
from crypto_vault import encrypt_field


class TestLocalAnalytics(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database and charts directory
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.charts_dir = tempfile.mkdtemp()
        
        # Initialize SQLite tables
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. daily_summary
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
        
        # 2. ledger_entries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount TEXT,
                type TEXT,
                remarks TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 3. daily_ledger
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

    def tearDown(self):
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
            
        shutil.rmtree(self.charts_dir, ignore_errors=True)

    def _seed_data(self):
        """Seeds typical operational data into the test database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Seed daily_summary (Volume Trend data)
        summaries = [
            ("2026-05-20", 1200.5, 950.0, 150000.0, 30000.0, 10.0, 1),
            ("2026-05-21", 1350.0, 1020.5, 170000.0, 40000.0, 10.0, 1),
            ("2026-05-22", 1100.2, 890.8, 140000.0, 25000.0, 10.0, 1),
            ("2026-05-23", 1450.6, 1150.2, 195000.0, 50000.0, 10.0, 1),
            ("2026-05-24", 1500.0, 1200.0, 210000.0, 60000.0, 10.0, 1),
            ("2026-05-25", 1300.0, 980.0, 165000.0, 35000.0, 10.0, 1),
        ]
        for date, hsd, ms, cash, credit, test_ded, verified in summaries:
            cursor.execute("""
                INSERT INTO daily_summary 
                (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (date, hsd, ms, cash, credit, test_ded, verified))
            
        # Seed ledger_entries (Credit Concentration data, encrypted)
        entries = [
            ("2026-05-20", encrypt_field("Gopalram Ji Dhaba"), "HR-38-1234", encrypt_field(15000.0), "udhaar", "filled"),
            ("2026-05-20", encrypt_field("Jagveer Ji Dhaba"), "HR-38-5678", encrypt_field(12000.0), "udhaar", "filled"),
            ("2026-05-21", encrypt_field("Gopalram Ji Dhaba"), "N/A", encrypt_field(5000.0), "payment", "partial pay"),
            ("2026-05-22", encrypt_field("Sher-e-Punjab Dhaba"), "HR-38-9012", encrypt_field(25000.0), "udhaar", "filled"),
            ("2026-05-23", encrypt_field("Jagveer Ji Dhaba"), "HR-38-5678", encrypt_field(8000.0), "udhaar", "filled"),
            ("2026-05-24", encrypt_field("A-1 Logistics"), "HR-55-9999", encrypt_field(45000.0), "udhaar", "bulk diesel"),
            # Seed plain text/unencrypted values to test decryption graceful fallback
            ("2026-05-25", "Plain Customer", "HR-38-7777", "10000.0", "udhaar", "plain entry"),
        ]
        for date, party, vehicle, amount, r_type, remarks in entries:
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (date, party, vehicle, amount, r_type, remarks))
            
        # Seed daily_ledger (Cash Flow Composition data)
        ledgers = [
            ("2026-05-20", 2150.5, 150000.0, 70000.0, 30000.0, 10000.0, 10000.0, 30000.0, 1000.0, "valid"),
            ("2026-05-21", 2370.5, 170000.0, 80000.0, 35000.0, 5000.0, 10000.0, 40000.0, 2000.0, "valid"),
            ("2026-05-22", 1991.0, 140000.0, 65000.0, 25000.0, 15000.0, 10000.0, 25000.0, 1500.0, "valid"),
        ]
        for date, sales_liters, amt_inr, cash_t, upi_t, paytm_t, card_t, udhaar_t, exp_t, val_status in ledgers:
            cursor.execute("""
                INSERT INTO daily_ledger 
                (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, expenses_amount, validation_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date, sales_liters, amt_inr, cash_t, upi_t, paytm_t, card_t, udhaar_t, exp_t, val_status))
            
        conn.commit()
        conn.close()

    def test_generate_all_charts_empty_db(self):
        """Verifies charts render cleanly even when database is completely empty (fallbacks check)."""
        paths = generate_all_charts(db_path=self.db_path, charts_dir=self.charts_dir)
        
        # Verify dictionary returns paths for all 3 charts
        self.assertIn("volume_trend", paths)
        self.assertIn("credit_concentration", paths)
        self.assertIn("cash_flow_composition", paths)
        
        # Verify files were created and are not empty
        for name, path in paths.items():
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.path.getsize(path) > 0)
            self.assertEqual(os.path.dirname(path), self.charts_dir)

    def test_generate_all_charts_with_data(self):
        """Verifies charts render perfectly with complete databases and decrypted values."""
        self._seed_data()
        paths = generate_all_charts(db_path=self.db_path, charts_dir=self.charts_dir)
        
        # Verify paths and files exist
        self.assertIn("volume_trend", paths)
        self.assertIn("credit_concentration", paths)
        self.assertIn("cash_flow_composition", paths)
        
        for name, path in paths.items():
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.path.getsize(path) > 0)


if __name__ == "__main__":
    unittest.main()
