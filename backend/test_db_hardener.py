import os
import sys
import sqlite3
import unittest

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import db_hardener

class TestDatabaseHardener(unittest.TestCase):
    def setUp(self):
        self.test_db_path = os.path.join(BACKEND_DIR, "test_hardener.db")
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_configure_connection(self):
        """Verifies that configure_connection applies WAL, NORMAL sync, and strict foreign keys."""
        # Use original connect to get an unhardened connection first (or patch connection)
        conn = sqlite3._original_connect(self.test_db_path)
        
        # Apply configurations
        db_hardener.configure_connection(conn)
        
        cursor = conn.cursor()
        
        # Verify journal_mode is WAL
        cursor.execute("PRAGMA journal_mode;")
        self.assertEqual(cursor.fetchone()[0].lower(), "wal")
        
        # Verify synchronous is NORMAL (1)
        cursor.execute("PRAGMA synchronous;")
        self.assertEqual(cursor.fetchone()[0], 1)
        
        # Verify foreign keys is ON (1)
        cursor.execute("PRAGMA foreign_keys;")
        self.assertEqual(cursor.fetchone()[0], 1)
        
        conn.close()

    def test_intercepted_connection(self):
        """Verifies that the patched sqlite3.connect automatically applies the hardener PRAGMAs."""
        # Open connection using standard connect call (which is patched)
        conn = sqlite3.connect(self.test_db_path)
        
        cursor = conn.cursor()
        
        # Verify journal_mode is WAL
        cursor.execute("PRAGMA journal_mode;")
        self.assertEqual(cursor.fetchone()[0].lower(), "wal")
        
        # Verify synchronous is NORMAL (1)
        cursor.execute("PRAGMA synchronous;")
        self.assertEqual(cursor.fetchone()[0], 1)
        
        # Verify foreign keys is ON (1)
        cursor.execute("PRAGMA foreign_keys;")
        self.assertEqual(cursor.fetchone()[0], 1)
        
        conn.close()

    def test_execute_wal_checkpoint(self):
        """Verifies that execute_wal_checkpoint successfully truncates the WAL log and returns stats."""
        # Create a database and write some dummy data to create a WAL file
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY, val TEXT);")
        cursor.execute("INSERT INTO dummy (val) VALUES ('test');")
        conn.commit()
        conn.close()
        
        # Execute checkpoint
        res = db_hardener.execute_wal_checkpoint(self.test_db_path)
        
        self.assertEqual(res.get("status"), "success")
        self.assertIn("busy", res)
        self.assertIn("log", res)
        self.assertIn("checkpointed", res)

if __name__ == "__main__":
    unittest.main()
