#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Multi-Station Workspace Profile Isolation.
Asserts directory structures, connection re-binding, dynamic schema updates, and isolated transactions.
"""

import os
import sys
import shutil
import unittest
import sqlite3
from pathlib import Path

# Add backend dir to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import workspace_manager
import main
import migrations

class TestWorkspaceManager(unittest.TestCase):
    def setUp(self):
        # We will use temporary workspace profiles for testing
        self.profile1 = "test_station_1"
        self.profile2 = "test_station_2"
        self.root_dir = Path(__file__).resolve().parent.parent
        
        # Clean any old test profiles
        self.cleanup_workspaces()

    def tearDown(self):
        # Restore default workspace profile environment
        os.environ.pop("ACTIVE_WORKSPACE_PROFILE", None)
        os.environ.pop("EXPORT_EXCEL_PATH", None)
        # Restore main.DB_PATH to original relative path
        workspace_manager.rebind_all_modules("pump_station_1")
        self.cleanup_workspaces()

    def cleanup_workspaces(self):
        for profile in [self.profile1, self.profile2]:
            w_dir = self.root_dir / "workspaces" / profile
            if w_dir.exists():
                shutil.rmtree(w_dir)

    def test_workspace_folder_tree_generation(self):
        """Verify initialize_active_workspace builds separate structural directories."""
        workspace_manager.initialize_active_workspace(self.profile1)
        w_dir = self.root_dir / "workspaces" / self.profile1
        
        self.assertTrue((w_dir / "database").exists())
        self.assertTrue((w_dir / "processed_images").exists())
        self.assertTrue((w_dir / "pump_exports").exists())
        self.assertTrue((w_dir / "pump_exports" / "charts").exists())

    def test_workspace_path_resolution(self):
        """Verify correct path resolution mapping."""
        paths = workspace_manager.get_workspace_paths(self.profile1)
        w_dir = self.root_dir / "workspaces" / self.profile1
        
        self.assertEqual(paths["database"], str((w_dir / "database" / "pump_accounts.db").resolve()))
        self.assertEqual(paths["processed_images"], str((w_dir / "processed_images").resolve()))
        self.assertEqual(paths["excel_path"], str((w_dir / "pump_exports" / "Pump_Accounts.xlsx").resolve()))

    def test_dynamic_module_rebinding(self):
        """Verify rebind_all_modules updates variables in loaded modules on the fly."""
        workspace_manager.rebind_all_modules(self.profile1)
        
        # Ensure main.DB_PATH is updated
        self.assertEqual(main.DB_PATH, workspace_manager.get_workspace_paths(self.profile1)["database"])
        self.assertEqual(main.EXCEL_PATH, workspace_manager.get_workspace_paths(self.profile1)["excel_path"])
        self.assertEqual(main.processed_dir, workspace_manager.get_workspace_paths(self.profile1)["processed_images"])
        
        # Verify environment variable
        self.assertEqual(os.environ.get("ACTIVE_WORKSPACE_PROFILE"), self.profile1)

    def test_workspace_switching_and_schema_initialization(self):
        """Verify switch_active_workspace creates new databases and applies migrations on the fly."""
        workspace_manager.switch_active_workspace(self.profile1)
        db_path = main.DB_PATH
        
        # Check DB file exists
        self.assertTrue(os.path.exists(db_path))
        
        # Check if tables are created and version is at current max (>= 6)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        version = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(version)
        self.assertGreaterEqual(version[0], 6)

    def test_workspace_data_isolation(self):
        """Verify data written to test_station_1 is fully isolated from test_station_2."""
        # 1. Setup workspace 1 and insert a record
        workspace_manager.switch_active_workspace(self.profile1)
        db1_path = main.DB_PATH
        
        conn1 = sqlite3.connect(db1_path)
        cursor1 = conn1.cursor()
        cursor1.execute("INSERT INTO daily_summary (date, total_cash_calculated) VALUES ('2026-06-01', 150000.0)")
        conn1.commit()
        conn1.close()
        
        # 2. Setup workspace 2 and query
        workspace_manager.switch_active_workspace(self.profile2)
        db2_path = main.DB_PATH
        
        conn2 = sqlite3.connect(db2_path)
        cursor2 = conn2.cursor()
        cursor2.execute("SELECT COUNT(*) FROM daily_summary WHERE date = '2026-06-01'")
        count = cursor2.fetchone()[0]
        conn2.close()
        
        # Workspace 2 must have 0 records for that date (isolated)
        self.assertEqual(count, 0)
        
        # Go back to Workspace 1 and verify data exists
        workspace_manager.switch_active_workspace(self.profile1)
        conn1 = sqlite3.connect(db1_path)
        cursor1 = conn1.cursor()
        cursor1.execute("SELECT total_cash_calculated FROM daily_summary WHERE date = '2026-06-01'")
        val = cursor1.fetchone()[0]
        conn1.close()
        self.assertEqual(val, 150000.0)

if __name__ == "__main__":
    unittest.main()
