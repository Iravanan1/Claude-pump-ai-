#!/usr/bin/env python3
"""
Unit tests for db_vacuum.py.
"""

import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from db_vacuum import execute_db_vacuum, execute_db_vacuum_background

class TestDBVacuum(unittest.TestCase):
    
    @patch("sqlite3.connect")
    def test_execute_db_vacuum_queries(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        res = execute_db_vacuum("/fake/path.db")
        self.assertTrue(res)
        
        # Verify it opened the correct database
        mock_connect.assert_called_once_with("/fake/path.db")
        
        # Verify it executed PRAGMA auto_vacuum, VACUUM, and FTS optimize queries
        mock_conn.execute.assert_any_call("PRAGMA auto_vacuum = INCREMENTAL;")
        mock_conn.execute.assert_any_call("VACUUM;")
        mock_conn.execute.assert_any_call("INSERT INTO ledger_fts(ledger_fts) VALUES('optimize');")
        
        # Verify it closed the connection
        mock_conn.close.assert_called_once()

    @patch("sqlite3.connect")
    def test_execute_db_vacuum_fts_skipped_gracefully(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        # Make the FTS optimization call raise OperationalError
        def custom_execute(query, *args, **kwargs):
            if "ledger_fts" in query:
                raise sqlite3.OperationalError("no such table: ledger_fts")
            return MagicMock()
            
        mock_conn.execute.side_effect = custom_execute
        
        res = execute_db_vacuum("/fake/path.db")
        self.assertTrue(res) # Should still return True gracefully!
        
        # Verify close is still called
        mock_conn.close.assert_called_once()

    @patch("db_vacuum.execute_db_vacuum")
    def test_execute_db_vacuum_background(self, mock_vacuum):
        thread = execute_db_vacuum_background("/fake/path.db")
        thread.join(timeout=1.0) # Wait for thread to finish
        self.assertFalse(thread.is_alive())
        mock_vacuum.assert_called_once_with("/fake/path.db")

if __name__ == "__main__":
    unittest.main()
