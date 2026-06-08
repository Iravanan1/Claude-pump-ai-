"""
Unit tests for interlock_checker.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil
import json

import crypto_vault
import interlock_checker

class TestInterlockChecker(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Configure master key for cryptography
        os.environ["PUMP_AI_MASTER_KEY"] = "test_interlock_secret_key"
        crypto_vault._fernet = None
        
        # Initialize tables
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
        conn.commit()
        conn.close()

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        crypto_vault._fernet = None

    def test_verify_chronological_continuity_no_preceding(self):
        # Test empty database behavior
        res = interlock_checker.verify_chronological_continuity(
            target_date_string="2026-06-01",
            current_nozzles=[{"nozzle_name": "MS-1", "opening": 100.0, "closing": 200.0}],
            db_path=self.test_db
        )
        self.assertEqual(res["status"], "no_preceding_record")
        self.assertIsNone(res["preceding_date"])
        self.assertEqual(len(res["discrepancies"]), 0)

    def test_verify_chronological_continuity_balanced(self):
        # Insert preceding day committed ledger entry
        prec_raw = {
            "image_url": "http://localhost:8000/uploaded_raw_photos/day1.png",
            "nozzles": [
                {"nozzle_name": "MS-1", "opening": 50.0, "closing": 100.0},
                {"nozzle_name": "HSD-1", "opening": 150.0, "closing": 300.0}
            ]
        }
        encrypted_prec = crypto_vault.encrypt_raw_data(prec_raw)
        
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_ledger (date, raw_data) VALUES ('2026-06-01', ?)
        """, (json.dumps(encrypted_prec),))
        conn.commit()
        conn.close()
        
        # Current day nozzles: MS-1 opening is 100 (locks with prec closing 100), HSD-1 opening is 300 (locks with prec closing 300)
        current_nozzles = [
            {"nozzle_name": "MS-1", "opening": 100.0, "closing": 150.0},
            {"nozzle_name": "HSD-1", "opening": 300.0, "closing": 400.0}
        ]
        
        res = interlock_checker.verify_chronological_continuity(
            target_date_string="2026-06-02",
            current_nozzles=current_nozzles,
            db_path=self.test_db
        )
        
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["preceding_date"], "2026-06-01")
        self.assertEqual(res["preceding_image_url"], "http://localhost:8000/uploaded_raw_photos/day1.png")
        self.assertEqual(len(res["discrepancies"]), 0)

    def test_verify_chronological_continuity_discrepancy(self):
        # Insert preceding day committed ledger entry
        prec_raw = {
            "image_url": "http://localhost:8000/uploaded_raw_photos/day1.png",
            "nozzles": [
                {"nozzle_name": "MS-1", "opening": 50.0, "closing": 100.0},
                {"nozzle_name": "HSD-1", "opening": 150.0, "closing": 300.0}
            ]
        }
        encrypted_prec = crypto_vault.encrypt_raw_data(prec_raw)
        
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_ledger (date, raw_data) VALUES ('2026-06-01', ?)
        """, (json.dumps(encrypted_prec),))
        conn.commit()
        conn.close()
        
        # Current day nozzles:
        # MS-1 opening is 105.5 (mismatch with prec closing 100 by +5.5 Liters)
        # HSD-1 opening is 300 (locks with prec closing 300)
        current_nozzles = [
            {"nozzle_name": "MS-1", "opening": 105.5, "closing": 150.0},
            {"nozzle_name": "HSD-1", "opening": 300.0, "closing": 400.0}
        ]
        
        res = interlock_checker.verify_chronological_continuity(
            target_date_string="2026-06-02",
            current_nozzles=current_nozzles,
            db_path=self.test_db
        )
        
        self.assertEqual(res["status"], "discrepancy")
        self.assertEqual(res["preceding_date"], "2026-06-01")
        self.assertEqual(res["preceding_image_url"], "http://localhost:8000/uploaded_raw_photos/day1.png")
        self.assertEqual(len(res["discrepancies"]), 1)
        
        disc = res["discrepancies"][0]
        self.assertEqual(disc["nozzle_name"], "MS-1")
        self.assertEqual(disc["current_opening"], 105.5)
        self.assertEqual(disc["preceding_closing"], 100.0)
        self.assertEqual(disc["variance"], 5.5)

if __name__ == "__main__":
    unittest.main()
