import os
import sys
import shutil
import sqlite3
import unittest
import pandas as pd
from datetime import datetime

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import self_healer
import init_db

class TestDatabaseSelfHealer(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(BACKEND_DIR, "test_self_healer_sandbox")
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.test_db_path = os.path.join(self.test_dir, "test_ledger.db")
        self.test_shadow_dir = os.path.join(self.test_dir, "test_shadow_mirror")
        
        # Patch self_healer constants and DB references
        self.original_shadow_dir = self_healer.SHADOW_DIR
        self_healer.SHADOW_DIR = self.test_shadow_dir
        self.original_init_db = init_db.DB_PATH
        
        # Initialize a healthy database
        init_db.DB_PATH = self.test_db_path
        init_db.initialize_database()

    def tearDown(self):
        # Restore paths
        self_healer.SHADOW_DIR = self.original_shadow_dir
        init_db.DB_PATH = self.original_init_db
        
        # Cleanup sandbox directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_integrity_check_healthy(self):
        """Verifies that a freshly initialized database passes integrity checks."""
        self.assertTrue(self_healer.perform_integrity_check(self.test_db_path))

    def test_integrity_check_corrupted(self):
        """Verifies that a corrupted (garbage) database fails integrity checks."""
        # Corrupt the database by writing non-SQLite header junk
        with open(self.test_db_path, "wb") as f:
            f.write(b"CORRUPTED_NON_SQLITE_GARBAGE_DATA_1234567890")
            
        self.assertFalse(self_healer.perform_integrity_check(self.test_db_path))

    def test_shadow_cloning_and_restore(self):
        """Verifies that shadow clone dumps the table state and auto-patches/re-populates it on failure."""
        # Insert test data into healthy database
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, is_verified) VALUES (?, ?, ?, ?)",
            ("2026-06-01", 1500.5, 2200.0, 1)
        )
        cursor.execute(
            "INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks) VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-06-01", "Party A", "HR-38-9999", 5000.0, "udhaar", "Manual entry")
        )
        conn.commit()
        conn.close()
        
        # Save a verified shadow clone mirror
        clone_file = self_healer.save_shadow_mirror(self.test_db_path, self.test_shadow_dir)
        self.assertTrue(os.path.exists(clone_file))
        
        # Corrupt the active database
        with open(self.test_db_path, "wb") as f:
            f.write(b"CORRUPTED_DB_DUE_TO_POWER_OUTAGE")
            
        # Verify it fails integrity check
        self.assertFalse(self_healer.perform_integrity_check(self.test_db_path))
        
        # Run self-healing recovery sequence
        recovered = self_healer.auto_heal_if_corrupted(self.test_db_path, self.test_shadow_dir)
        self.assertTrue(recovered)
        
        # Verify isolated corrupted file exists
        corrupted_files = [f for f in os.listdir(self.test_dir) if ".db.corrupted_" in f]
        self.assertTrue(len(corrupted_files) >= 1)
        
        # Assert database is now healthy and populated
        self.assertTrue(self_healer.perform_integrity_check(self.test_db_path))
        
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT date, total_hsd_liters, total_ms_liters, is_verified FROM daily_summary")
        summary = cursor.fetchone()
        self.assertEqual(summary[0], "2026-06-01")
        self.assertEqual(summary[1], 1500.5)
        self.assertEqual(summary[2], 2200.0)
        self.assertEqual(summary[3], 1)
        
        cursor.execute("SELECT date, party_name, vehicle_wheel_no, amount, type, remarks FROM ledger_entries")
        entry = cursor.fetchone()
        self.assertEqual(entry[0], "2026-06-01")
        self.assertEqual(entry[1], "Party A")
        self.assertEqual(entry[2], "HR-38-9999")
        self.assertEqual(entry[3], 5000.0)
        self.assertEqual(entry[4], "udhaar")
        self.assertEqual(entry[5], "Manual entry")
        
        conn.close()

if __name__ == "__main__":
    unittest.main()
