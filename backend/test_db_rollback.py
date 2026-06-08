#!/usr/bin/env python3
"""
Unit tests for db_rollback.py.
"""

import os
import sys
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import db_rollback

class TestDBRollback(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for our test files
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ledger.db")
        self.snapshots_dir = os.path.join(self.test_dir, "snapshots")
        
        # Patch the module-level SNAPSHOTS_DIR to our temp directory
        self.original_snapshots_dir = db_rollback.SNAPSHOTS_DIR
        db_rollback.SNAPSHOTS_DIR = self.snapshots_dir
        
        # Create a mock database with a table and a row
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO test_table (name) VALUES ('initial_record')")
        conn.commit()
        conn.close()

    def tearDown(self):
        # Restore the original SNAPSHOTS_DIR
        db_rollback.SNAPSHOTS_DIR = self.original_snapshots_dir
        # Clean up temp folder
        shutil.rmtree(self.test_dir)

    def test_create_pre_batch_snapshot_success(self):
        # Create a pre-batch snapshot
        snap_path = db_rollback.create_pre_batch_snapshot(db_path=self.db_path, label="test_batch")
        
        # Verify snapshot file exists
        self.assertTrue(os.path.isfile(snap_path))
        self.assertIn("snapshots", snap_path)
        self.assertIn("test_batch.bak", snap_path)
        
        # Verify size matches or is close
        self.assertEqual(os.path.getsize(snap_path), os.path.getsize(self.db_path))
        
        # Verify content integrity by reading it
        conn = sqlite3.connect(snap_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM test_table")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "initial_record")
        conn.close()

    def test_create_pre_batch_snapshot_source_not_found(self):
        # Try to snapshot a database that does not exist
        fake_db = os.path.join(self.test_dir, "nonexistent.db")
        with self.assertRaises(RuntimeError):
            db_rollback.create_pre_batch_snapshot(db_path=fake_db)

    def test_list_available_snapshots(self):
        # Create multiple snapshots
        db_rollback.create_pre_batch_snapshot(db_path=self.db_path, label="first")
        # Ensure timestamp might change, or just check multiple are listed
        db_rollback.create_pre_batch_snapshot(db_path=self.db_path, label="second")
        
        snaps = db_rollback.list_available_snapshots()
        self.assertEqual(len(snaps), 2)
        
        # Newest first
        self.assertIn("second", snaps[0]["filename"])
        self.assertIn("first", snaps[1]["filename"])
        
        self.assertEqual(snaps[0]["label"], "second")
        self.assertEqual(snaps[1]["label"], "first")
        
        self.assertEqual(snaps[0]["size_bytes"], os.path.getsize(self.db_path))

    def test_prune_old_snapshots(self):
        # Create 5 snapshots
        for i in range(5):
            db_rollback.create_pre_batch_snapshot(db_path=self.db_path, label=f"snap_{i}")
            
        # Verify we have 5
        self.assertEqual(len(db_rollback.list_available_snapshots()), 5)
        
        # Prune to keep 2
        deleted = db_rollback.prune_old_snapshots(max_keep=2)
        self.assertEqual(deleted, 3)
        
        # Verify only 2 remain
        snaps = db_rollback.list_available_snapshots()
        self.assertEqual(len(snaps), 2)
        # Should be the most recent ones (index 4 and index 3)
        self.assertEqual(snaps[0]["label"], "snap_4")
        self.assertEqual(snaps[1]["label"], "snap_3")

    @patch("db_rollback._reinit_app_connections")
    def test_rollback_to_last_snapshot_success(self, mock_reinit):
        # 1. Take a snapshot of the initial state
        db_rollback.create_pre_batch_snapshot(db_path=self.db_path, label="good_state")
        
        # 2. Modify the active database (simulate corruption or batch run)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO test_table (name) VALUES ('corrupted_record')")
        conn.commit()
        
        # Check active DB has 2 records
        cursor.execute("SELECT name FROM test_table")
        self.assertEqual(len(cursor.fetchall()), 2)
        conn.close()
        
        # 3. Perform rollback
        result = db_rollback.rollback_to_last_snapshot(db_path=self.db_path, reinitialize_connections=True)
        
        # 4. Verify result summary
        self.assertEqual(result["status"], "ok")
        self.assertIn("good_state.bak", result["snapshot_used"])
        self.assertEqual(result["active_db"], self.db_path)
        
        # 5. Verify active database was rolled back (only initial record exists)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM test_table")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "initial_record")
        conn.close()
        
        # 6. Verify evicted DB backup file exists
        evicted_path = self.db_path + ".rollback_evicted"
        self.assertTrue(os.path.isfile(evicted_path))
        
        # Verify evicted DB has the corrupted record
        conn_evicted = sqlite3.connect(evicted_path)
        cursor_evicted = conn_evicted.cursor()
        cursor_evicted.execute("SELECT name FROM test_table")
        self.assertEqual(len(cursor_evicted.fetchall()), 2)
        conn_evicted.close()
        
        # Verify connection re-initialization was called
        mock_reinit.assert_called_once_with(self.db_path)

    def test_rollback_to_last_snapshot_no_snapshots(self):
        # Attempt to roll back when there are no snapshots
        with self.assertRaises(RuntimeError) as context:
            db_rollback.rollback_to_last_snapshot(db_path=self.db_path)
        self.assertIn("No snapshots found", str(context.exception))

if __name__ == "__main__":
    unittest.main()
