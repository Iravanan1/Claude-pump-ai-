import os
import sys
import unittest
import sqlite3

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import optimize

TEST_DB = os.path.join(BACKEND_DIR, "test_optimize.db")

class TestOptimize(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
            
        # Create mock tables
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            party_name TEXT,
            vehicle_wheel_no TEXT,
            amount REAL DEFAULT 0.0,
            type TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    def test_optimize_database(self):
        """Verifies indexes are created successfully and database VACUUM/ANALYZE executes."""
        success = optimize.optimize_database(TEST_DB)
        self.assertTrue(success)
        
        # Connect and check indexes
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [r[0] for r in cursor.fetchall()]
        
        self.assertIn("idx_ledger_entries_date", indexes)
        self.assertIn("idx_ledger_entries_party_nocase", indexes)
        self.assertIn("idx_ledger_entries_vehicle", indexes)
        
        conn.close()

if __name__ == "__main__":
    unittest.main()
