#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Schema Migrations.
Asserts version tracking, sequential upgrades, idempotency, and fail-safe transactional rollbacks.
"""

import os
import unittest
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import migrations

class TestDatabaseMigrations(unittest.TestCase):
    def setUp(self):
        # Create a temporary sandbox database for each test to keep isolation clean
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        
    def tearDown(self):
        # Close file descriptor and remove temporary file cleanly
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_clean_migration_upgrades_version_to_current(self):
        """Verifies that a clean migration setup runs successfully, adds all columns,
        and sets the sys_version to the current maximum (6 — accounting_head)."""
        # 1. Run migrations
        migrations.apply_schema_updates(self.temp_db_path)
        
        # 2. Assert sys_version contains current max version (>= 6)
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row[0], 6)
        
        # 3. Assert column transaction_source is present in ledger_entries with default 'manual'
        cursor.execute("PRAGMA table_info(ledger_entries)")
        cols = {c[1]: (c[2], c[4]) for c in cursor.fetchall()} # name -> (type, default_val)
        
        self.assertIn("transaction_source", cols)
        self.assertEqual(cols["transaction_source"][0].upper(), "TEXT")
        
        # In SQLite, table_info default value is returned as string literal, e.g. "'manual'"
        default_val = cols["transaction_source"][1]
        self.assertIn("manual", default_val)

        # Assert version 3 FIFO columns are present
        self.assertIn("payment_status", cols)
        self.assertIn("amount_remaining", cols)
        self.assertIn("linked_payment_id", cols)

        # Assert version 4 special contracts and discount columns are present
        self.assertIn("base_amount", cols)
        self.assertIn("discount_applied", cols)
        self.assertIn("base_rate", cols)

        # Assert version 5 columns in daily_summary are present
        cursor.execute("PRAGMA table_info(daily_summary)")
        ds_cols = {c[1]: (c[2], c[4]) for c in cursor.fetchall()}
        self.assertIn("meter_replaced", ds_cols)
        self.assertIn("replacement_offset_liters", ds_cols)

        # Assert version 6 accounting_head column is present in ledger_entries
        self.assertIn("accounting_head", cols)
        
        # 4. Insert a test record to ensure default value mapping
        cursor.execute("""
        INSERT INTO ledger_entries (date, party_name, amount, type, remarks)
        VALUES ('2026-05-31', 'Gopalram', 1500.0, 'udhaar', 'test')
        """)
        conn.commit()
        
        cursor.execute("SELECT transaction_source FROM ledger_entries WHERE party_name = 'Gopalram'")
        inserted_row = cursor.fetchone()
        self.assertIsNotNone(inserted_row)
        self.assertEqual(inserted_row[0], 'manual')
        
        conn.close()

    def test_migration_idempotence(self):
        """Verifies that running apply_schema_updates repeatedly is safe and doesn't crash or duplicate columns."""
        # Run first time
        migrations.apply_schema_updates(self.temp_db_path)
        
        # Run second time
        try:
            migrations.apply_schema_updates(self.temp_db_path)
        except Exception as e:
            self.fail(f"Idempotency check failed: running migrations a second time raised an exception: {str(e)}")
            
        # Verify version remains at current max (>= 6)
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        self.assertGreaterEqual(cursor.fetchone()[0], 6)
        conn.close()

    @patch("migrations.sqlite3.connect")
    def test_fail_safe_rollback_on_migration_error(self, mock_connect):
        """
        Simulates an error midway through schema alterations to verify that the active database transaction 
        is rolled back cleanly, keeping database version and historical ledger entries uncorrupted.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Simulate that database is at version 1
        mock_cursor.fetchone.side_effect = [
            (1,), # sys_version lookup SELECT value FROM sys_version
        ]
        
        # Force cursor.execute to throw an error when altering table
        def faulty_execute(sql, *args, **kwargs):
            if "ALTER TABLE ledger_entries" in sql:
                raise sqlite3.OperationalError("Simulated write lock or structural failure")
            return MagicMock()
            
        mock_cursor.execute.side_effect = faulty_execute
        
        # Call migrations - it should raise OperationalError and rollback
        with self.assertRaises(sqlite3.OperationalError):
            migrations.apply_schema_updates(self.temp_db_path)
            
        # Assert rollback was triggered
        mock_conn.rollback.assert_called_once()

if __name__ == "__main__":
    unittest.main()
