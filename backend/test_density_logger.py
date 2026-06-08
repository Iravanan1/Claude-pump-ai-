"""
Unit test suite for density_logger.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil

import density_logger

class TestDensityLogger(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Initialize database schema
        density_logger.init_density_db(self.test_db)

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_init_db_creates_table_and_index(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='density_register'")
        self.assertIsNotNone(cursor.fetchone())
        
        # Verify index exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_density_register_date'")
        self.assertIsNotNone(cursor.fetchone())
        
        conn.close()

    def test_linear_conversion_math(self):
        # 1. HSD (Diesel - coefficient 0.7)
        # Temp = 25°C, Delta T = 10°C, Obs = 830.0
        # Expected = 830.0 + 0.7 * 10 = 837.0
        val = density_logger.convert_density_to_15c(830.0, 25.0, "HSD", method="linear")
        self.assertEqual(val, 837.0)

        # Temp = 5°C, Delta T = -10°C, Obs = 850.0
        # Expected = 850.0 + 0.7 * -10 = 843.0
        val = density_logger.convert_density_to_15c(850.0, 5.0, "HSD", method="linear")
        self.assertEqual(val, 843.0)

        # 2. MS (Petrol - coefficient 0.9)
        # Temp = 25°C, Delta T = 10°C, Obs = 730.0
        # Expected = 730.0 + 0.9 * 10 = 739.0
        val = density_logger.convert_density_to_15c(730.0, 25.0, "MS", method="linear")
        self.assertEqual(val, 739.0)

    def test_astm_conversion_math(self):
        # Temp = 15°C -> no correction
        val = density_logger.convert_density_to_15c(830.0, 15.0, "HSD", method="astm")
        self.assertEqual(val, 830.0)

        # Non-linear conversion check for HSD (Diesel)
        val_hsd = density_logger.convert_density_to_15c(830.0, 25.0, "HSD", method="astm")
        # Standard value at 25°C is around 836.5 to 837.5
        self.assertTrue(836.0 <= val_hsd <= 838.0)

        # Non-linear conversion check for MS (Petrol)
        val_ms = density_logger.convert_density_to_15c(730.0, 25.0, "MS", method="astm")
        # Standard value at 25°C is around 738.5 to 740.0
        self.assertTrue(738.0 <= val_ms <= 741.0)

    def test_save_and_retrieve_density_records(self):
        # 1. Save compliance record that passes (Variation is within +/- 3.0)
        # Observed: 830.0 at 25°C -> ASTM converted: ~836.85
        # Invoice ref: 835.0 -> variation is ~1.85 -> passes
        rec1 = density_logger.save_density_record(
            date_str="2026-06-01",
            product_type="HSD",
            temp=25.0,
            raw_density=830.0,
            invoice_ref=835.0,
            db_path=self.test_db,
            method="astm"
        )
        self.assertTrue(rec1["permissible_variation_passed"])
        self.assertTrue(abs(rec1["variation"]) <= 3.0)

        # 2. Save compliance record that fails (Variation exceeds +/- 3.0)
        # Observed: 830.0 at 25°C -> ASTM converted: ~836.85
        # Invoice ref: 842.0 -> variation is ~-5.15 -> fails
        rec2 = density_logger.save_density_record(
            date_str="2026-06-01",
            product_type="MS",
            temp=25.0,
            raw_density=730.0,
            invoice_ref=745.0,
            db_path=self.test_db,
            method="astm"
        )
        self.assertFalse(rec2["permissible_variation_passed"])

        # Fetch records from database
        records = density_logger.get_density_records(db_path=self.test_db)
        self.assertEqual(len(records), 2)
        
        # Chronological sorting: date descending, then product ascending ('HSD' before 'MS')
        self.assertEqual(records[0]["product_type"], "HSD")
        self.assertEqual(records[0]["permissible_variation_passed"], True)
        
        self.assertEqual(records[1]["product_type"], "MS")
        self.assertEqual(records[1]["permissible_variation_passed"], False)

    def test_save_density_record_idempotence(self):
        # Initial save
        density_logger.save_density_record("2026-06-01", "HSD", 25.0, 830.0, 835.0, db_path=self.test_db)
        
        # Override save
        density_logger.save_density_record("2026-06-01", "HSD", 25.0, 840.0, 835.0, db_path=self.test_db)
        
        records = density_logger.get_density_records(db_path=self.test_db)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["observed_density_raw"], 840.0)

if __name__ == "__main__":
    unittest.main()
