import os
import sys
import sqlite3
import shutil
import unittest
from unittest.mock import patch, MagicMock

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import bulk_importer
import init_db
import crypto_vault

class TestBulkImporter(unittest.TestCase):
    def setUp(self):
        # Use a separate test database path to avoid polluting the operational database
        self.original_db_path = bulk_importer.DB_PATH
        self.original_init_db_path = init_db.DB_PATH
        
        self.test_db_path = os.path.join(BACKEND_DIR, "test_ledger.db")
        bulk_importer.DB_PATH = self.test_db_path
        init_db.DB_PATH = self.test_db_path
        
        # Clean up database if it already exists
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
            
        # Create temp folders for testing
        self.test_photos_dir = os.path.join(BACKEND_DIR, "test_historical_photos")
        self.test_flagged_dir = os.path.join(BACKEND_DIR, "test_flagged_records")
        os.makedirs(self.test_photos_dir, exist_ok=True)
        os.makedirs(self.test_flagged_dir, exist_ok=True)
        
        # Override bulk importer constants for testing
        self.original_flagged_dir = bulk_importer.FLAGGED_DIR
        bulk_importer.FLAGGED_DIR = self.test_flagged_dir

    def tearDown(self):
        # Restore original paths
        bulk_importer.DB_PATH = self.original_db_path
        init_db.DB_PATH = self.original_init_db_path
        bulk_importer.FLAGGED_DIR = self.original_flagged_dir
        
        # Clean up files and folders
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
        if os.path.exists(self.test_photos_dir):
            shutil.rmtree(self.test_photos_dir)
        if os.path.exists(self.test_flagged_dir):
            shutil.rmtree(self.test_flagged_dir)

    def test_init_metadata_table(self):
        """Verifies that database metadata tables are correctly initialized."""
        bulk_importer.init_metadata_table()
        
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        # Check that processed_files exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='processed_files'")
        self.assertIsNotNone(cursor.fetchone(), "processed_files table was not created")
        
        # Check that daily_ledger exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_ledger'")
        self.assertIsNotNone(cursor.fetchone(), "daily_ledger table was not created")
        
        conn.close()

    def test_file_hashing_and_duplication(self):
        """Tests that file hashing detects duplicate files."""
        # Create a mock file
        test_file = os.path.join(self.test_photos_dir, "day_1.png")
        with open(test_file, "w") as f:
            f.write("mock_image_bytes_12345")
            
        hash1 = bulk_importer.calculate_file_hash(test_file)
        self.assertTrue(len(hash1) > 0)
        
        # Initialize tables
        bulk_importer.init_metadata_table()
        
        # Check duplication functions
        self.assertFalse(bulk_importer.is_already_processed("day_1.png", hash1))
        
        bulk_importer.record_processed_file("day_1.png", hash1)
        self.assertTrue(bulk_importer.is_already_processed("day_1.png", hash1))
        
        # Check different filename with same hash
        self.assertTrue(bulk_importer.is_already_processed("day_other.png", hash1))

    def test_commit_to_ledger_verified(self):
        """Verifies database insertion logic for a verified, math-balanced entry."""
        # 1. Initialize databases
        from init_db import initialize_database
        initialize_database() # Creates daily_summary and ledger_entries
        bulk_importer.init_metadata_table() # Creates processed_files and daily_ledger
        
        # Mock structured AI output
        mock_data = {
            "date": "2026-05-30",
            "total_calculated_liters_hsd": 100.5,
            "total_calculated_liters_ms": 200.5,
            "total_cash_calculated": 28000.0,
            "total_credit_sales": 8000.0,
            "total_testing_deductions": 5.0,
            "validation_status": "balanced",
            "mathematical_warnings": [],
            "nozzles": [
                {
                    "nozzle_name": "Nozzle 1 (HSD)",
                    "fuel_type": "HSD",
                    "opening": 1000.0,
                    "closing": 1105.5,
                    "net_sales_liters": 100.5,
                    "rate": 90.0,
                    "amount_calculated": 9045.0,
                    "is_valid": True
                }
            ],
            "credit_sales": [
                {
                    "party_name": "Ramesh Transport",
                    "vehicle_no": "HR-55-A-1234",
                    "amount": 8000.0,
                    "remarks": "HSD credit"
                }
            ],
            "cash_expenses": [
                {
                    "party_name": "Office Tea",
                    "amount": 200.0,
                    "remarks": "Tea"
                }
            ]
        }
        
        # Commit to ledger
        bulk_importer.commit_to_ledger(mock_data, is_verified=True)
        
        # Verify SQLite tables
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        # Check daily_summary
        cursor.execute("SELECT * FROM daily_summary WHERE date = '2026-05-30'")
        row_summary = cursor.fetchone()
        self.assertIsNotNone(row_summary)
        self.assertEqual(row_summary[1], 100.5) # total_hsd_liters
        self.assertEqual(row_summary[2], 200.5) # total_ms_liters
        self.assertEqual(row_summary[3], 28000.0) # total_cash_calculated
        self.assertEqual(row_summary[4], 8000.0) # total_credit_sales
        self.assertEqual(row_summary[5], 5.0) # total_testing_deductions
        self.assertEqual(row_summary[6], 1) # is_verified
        
        # Check ledger_entries
        cursor.execute("SELECT * FROM ledger_entries WHERE date = '2026-05-30' AND type = 'udhaar'")
        row_udhaar = cursor.fetchone()
        self.assertIsNotNone(row_udhaar)
        self.assertEqual(crypto_vault.decrypt_field(row_udhaar[2]), "Ramesh Transport")
        self.assertEqual(row_udhaar[3], "HR-55-A-1234")
        self.assertEqual(crypto_vault.decrypt_field(row_udhaar[4], return_type=float), 8000.0)
        
        cursor.execute("SELECT * FROM ledger_entries WHERE date = '2026-05-30' AND type = 'expense'")
        row_expense = cursor.fetchone()
        self.assertIsNotNone(row_expense)
        self.assertEqual(crypto_vault.decrypt_field(row_expense[2]), "Office Tea")
        self.assertEqual(crypto_vault.decrypt_field(row_expense[4], return_type=float), 200.0)
        
        # Check daily_ledger
        cursor.execute("SELECT * FROM daily_ledger WHERE date = '2026-05-30'")
        row_ledger = cursor.fetchone()
        self.assertIsNotNone(row_ledger)
        self.assertEqual(row_ledger[2], 301.0) # total_sales_liters (100.5 + 200.5)
        self.assertEqual(row_ledger[3], 28000.0) # total_amount_inr
        self.assertEqual(row_ledger[8], 8000.0) # udhaar_sales
        self.assertEqual(row_ledger[9], 200.0) # expenses_amount
        self.assertEqual(row_ledger[10], "valid") # validation_status
        
        conn.close()

    def test_commit_to_ledger_flagged(self):
        """Verifies database insertion logic for an unverified, math-discrepant entry."""
        from init_db import initialize_database
        initialize_database()
        bulk_importer.init_metadata_table()
        
        # Mock discrepant output
        mock_data = {
            "date": "2026-05-31",
            "total_calculated_liters_hsd": 50.0,
            "total_calculated_liters_ms": 0.0,
            "total_cash_calculated": 4500.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "math_discrepancy",
            "mathematical_warnings": ["Warning: Nozzle math difference"],
            "nozzles": [],
            "credit_sales": [],
            "cash_expenses": []
        }
        
        # Commit to ledger as flagged (is_verified = False)
        bulk_importer.commit_to_ledger(mock_data, is_verified=False)
        
        # Verify SQLite tables
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        # Check daily_summary
        cursor.execute("SELECT is_verified FROM daily_summary WHERE date = '2026-05-31'")
        row_summary = cursor.fetchone()
        self.assertEqual(row_summary[0], 0) # is_verified = 0
        
        # Check daily_ledger
        cursor.execute("SELECT validation_status FROM daily_ledger WHERE date = '2026-05-31'")
        row_ledger = cursor.fetchone()
        self.assertEqual(row_ledger[0], "needs_review") # validation_status = needs_review
        
        conn.close()

    @patch("image_guard.validate_image_clarity")
    @patch("bulk_importer.optimize_register_image")
    @patch("bulk_importer.analyze_register_sheet")
    def test_run_bulk_import_integration(self, mock_analyze, mock_optimize, mock_clarity):
        """Tests the end-to-end import loops, duplicate skips, and reviewer directory transfers."""
        mock_clarity.return_value = {"success": True, "status": "OK", "focus_score": 999.0, "contrast_score": 999.0}
        # Setup files
        file_valid = os.path.join(self.test_photos_dir, "day_100_balanced.png")
        file_flagged = os.path.join(self.test_photos_dir, "day_101_error.png")
        
        with open(file_valid, "w") as f:
            f.write("balanced_data_bytes")
        with open(file_flagged, "w") as f:
            f.write("discrepancy_data_bytes")
            
        # Mock optimize_register_image to return same paths
        mock_optimize.side_effect = lambda x: x
        
        # Mock analyze_register_sheet results
        mock_analyze.side_effect = [
            # Day 100: balanced
            {
                "date": "2026-06-01",
                "total_calculated_liters_hsd": 100.0,
                "total_calculated_liters_ms": 100.0,
                "total_cash_calculated": 18000.0,
                "total_credit_sales": 0.0,
                "total_testing_deductions": 0.0,
                "validation_status": "balanced",
                "mathematical_warnings": []
            },
            # Day 101: math warning
            {
                "date": "2026-06-02",
                "total_calculated_liters_hsd": 80.0,
                "total_calculated_liters_ms": 120.0,
                "total_cash_calculated": 19000.0,
                "total_credit_sales": 1000.0,
                "total_testing_deductions": 0.0,
                "validation_status": "math_discrepancy",
                "mathematical_warnings": ["Warning: opening meter mismatch"]
            }
        ]
        
        # Initialize test database
        from init_db import initialize_database
        initialize_database()
        
        # Run import
        bulk_importer.run_bulk_import(self.test_photos_dir)
        
        # Check database records
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT date, is_verified FROM daily_summary ORDER BY date ASC")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ("2026-06-01", 1)) # Verified
        self.assertEqual(rows[1], ("2026-06-02", 0)) # Unverified / needs review
        
        # Check processed_files table populated
        cursor.execute("SELECT COUNT(*) FROM processed_files")
        self.assertEqual(cursor.fetchone()[0], 2)
        
        conn.close()
        
        # Check flagged files folder copy
        flagged_files = os.listdir(self.test_flagged_dir)
        self.assertEqual(len(flagged_files), 1)
        self.assertIn("day_101_error.png", flagged_files)
        self.assertNotIn("day_100_balanced.png", flagged_files)

if __name__ == "__main__":
    unittest.main()
