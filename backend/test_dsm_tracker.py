"""
Unit test suite for dsm_tracker.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil
import json

import crypto_vault
import dsm_tracker

class TestDsmTracker(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Configure master key for cryptography (required for decryption checks)
        os.environ["PUMP_AI_MASTER_KEY"] = "test_dsm_secret_key"
        crypto_vault._fernet = None
        
        # Initialize DSM database schema
        dsm_tracker.init_dsm_db(self.test_db)

    def tearDown(self):
        # Cleanup temp assets
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        crypto_vault._fernet = None

    def test_init_db_creates_table(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dsm_shifts'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_save_and_retrieve_dsm_shift(self):
        # Save a new shift allocation
        dsm_tracker.save_dsm_shift(
            date_str="2026-06-01",
            shift_type="Day",
            dsm_name="Ramesh",
            assigned_nozzles="MS-1, MS-2",
            cash_handed_over=45000.0,
            digital_slips_value=1200.0,
            calculated_shortage_or_excess=-120.0,
            db_path=self.test_db
        )
        
        # Fetch shifts for the date
        shifts = dsm_tracker.get_dsm_shifts_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(shifts), 1)
        self.assertEqual(shifts[0]["dsm_name"], "Ramesh")
        self.assertEqual(shifts[0]["assigned_nozzles"], "MS-1, MS-2")
        self.assertEqual(shifts[0]["cash_handed_over"], 45000.0)
        self.assertEqual(shifts[0]["digital_slips_value"], 1200.0)
        self.assertEqual(shifts[0]["calculated_shortage_or_excess"], -120.0)
        
        # Verify UNIQUE constraint triggers REPLACE
        dsm_tracker.save_dsm_shift(
            date_str="2026-06-01",
            shift_type="Day",
            dsm_name="Ramesh",
            assigned_nozzles="MS-1", # Updated nozzles
            cash_handed_over=47000.0, # Updated cash
            digital_slips_value=1200.0,
            calculated_shortage_or_excess=50.0, # Updated discrepancy
            db_path=self.test_db
        )
        
        shifts_updated = dsm_tracker.get_dsm_shifts_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(shifts_updated), 1)
        self.assertEqual(shifts_updated[0]["assigned_nozzles"], "MS-1")
        self.assertEqual(shifts_updated[0]["cash_handed_over"], 47000.0)
        self.assertEqual(shifts_updated[0]["calculated_shortage_or_excess"], 50.0)

    def test_chronological_all_shifts(self):
        # Insert shifts out of order
        dsm_tracker.save_dsm_shift("2026-06-03", "Day", "Ramesh", "MS-1", 10000.0, db_path=self.test_db)
        dsm_tracker.save_dsm_shift("2026-06-01", "Night", "Suresh", "HSD-1", 20000.0, db_path=self.test_db)
        dsm_tracker.save_dsm_shift("2026-06-01", "Day", "Ramesh", "MS-1", 15000.0, db_path=self.test_db)
        
        all_shifts = dsm_tracker.get_all_dsm_shifts(db_path=self.test_db)
        self.assertEqual(len(all_shifts), 3)
        # Asserts chronological sorting
        self.assertEqual(all_shifts[0]["date"], "2026-06-01")
        self.assertEqual(all_shifts[0]["shift_type"], "Day")
        self.assertEqual(all_shifts[1]["date"], "2026-06-01")
        self.assertEqual(all_shifts[1]["shift_type"], "Night")
        self.assertEqual(all_shifts[2]["date"], "2026-06-03")

    def test_delete_shifts_by_date(self):
        dsm_tracker.save_dsm_shift("2026-06-01", "Day", "Ramesh", "MS-1", 10000.0, db_path=self.test_db)
        dsm_tracker.save_dsm_shift("2026-06-02", "Day", "Suresh", "HSD-1", 20000.0, db_path=self.test_db)
        
        dsm_tracker.delete_dsm_shifts_by_date("2026-06-01", db_path=self.test_db)
        
        self.assertEqual(len(dsm_tracker.get_dsm_shifts_by_date("2026-06-01", db_path=self.test_db)), 0)
        self.assertEqual(len(dsm_tracker.get_dsm_shifts_by_date("2026-06-02", db_path=self.test_db)), 1)

    def test_calculate_dsm_expected_sales(self):
        # Create a mock daily_ledger record with encrypted nozzles raw_data
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_ledger (
                date TEXT PRIMARY KEY,
                total_sales_liters REAL,
                total_amount_inr REAL,
                cash_tender REAL,
                upi_tender REAL,
                paytm_transfers REAL,
                card_tender REAL,
                udhaar_sales REAL,
                expenses_amount REAL,
                validation_status TEXT,
                raw_data TEXT
            )
        """)
        
        raw_payload = {
            "date": "2026-06-10",
            "nozzles": [
                {
                    "nozzle_name": "MS-1 (Petrol)",
                    "amount_calculated": 25000.0
                },
                {
                    "nozzle_name": "MS-2 (Petrol)",
                    "amount_calculated": 15000.0
                },
                {
                    "nozzle_name": "HSD-1 (Diesel)",
                    "amount_calculated": 30000.0
                }
            ]
        }
        
        encrypted_raw_data = crypto_vault.encrypt_raw_data(raw_payload)
        raw_json_str = json.dumps(encrypted_raw_data, ensure_ascii=False)
        
        cursor.execute("""
            INSERT INTO daily_ledger (date, raw_data) VALUES (?, ?)
        """, ("2026-06-10", raw_json_str))
        conn.commit()
        conn.close()
        
        # Test calculation for Ramesh (MS-1, MS-2)
        # Expected expected_sales = 25000.0 + 15000.0 = 40000.0
        sales = dsm_tracker.calculate_dsm_expected_sales("2026-06-10", ["MS-1", "MS-2"], db_path=self.test_db)
        self.assertEqual(sales, 40000.0)
        
        # Test calculation for Suresh (HSD-1)
        # Expected expected_sales = 30000.0
        sales_suresh = dsm_tracker.calculate_dsm_expected_sales("2026-06-10", ["HSD-1"], db_path=self.test_db)
        self.assertEqual(sales_suresh, 30000.0)
        
        # Test non-matching or empty nozzles
        sales_empty = dsm_tracker.calculate_dsm_expected_sales("2026-06-10", ["UNKNOWN-NOZZLE"], db_path=self.test_db)
        self.assertEqual(sales_empty, 0.0)

if __name__ == "__main__":
    unittest.main()
