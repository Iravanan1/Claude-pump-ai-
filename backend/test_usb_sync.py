import os
import sys
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Add backend to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import usb_sync

class TestUSBSync(unittest.TestCase):
    def setUp(self):
        # Create temp dirs representing source workspace and mock usb mount
        self.src_dir = tempfile.mkdtemp()
        self.usb_dir = tempfile.mkdtemp()
        
        # Save original reference methods
        self.original_get_active_source_files = usb_sync.get_active_source_files
        self.original_scan_for_external_mounts = usb_sync.scan_for_external_mounts
        
        # Setup mock active source files
        self.db_path = os.path.join(self.src_dir, "pump_accounts.db")
        with open(self.db_path, "w", encoding="utf-8") as f:
            f.write("mock database contents")
            
        self.cache_path = os.path.join(self.src_dir, "corrections_cache.json")
        with open(self.cache_path, "w", encoding="utf-8") as f:
            f.write('{"corrections": {}}')
            
        self.excel_path = os.path.join(self.src_dir, "ledger.xlsx")
        with open(self.excel_path, "w", encoding="utf-8") as f:
            f.write("mock excel sheet contents")
            
        # Define mock source file resolver
        def mock_get_active_source_files():
            return [
                ("database", self.db_path),
                ("cache", self.cache_path),
                ("excel", self.excel_path)
            ]
        usb_sync.get_active_source_files = mock_get_active_source_files
        
        # Define mock scanner that returns our mock usb mount directory
        def mock_scan_for_external_mounts(tags=None):
            return [self.usb_dir]
        usb_sync.scan_for_external_mounts = mock_scan_for_external_mounts
        
        # Reset global state trackers
        usb_sync.last_copied_states.clear()
        usb_sync.last_sync_info = {
            "status": "idle",
            "last_sync_time": None,
            "copied_files": []
        }
        usb_sync.subscribers.clear()

    def tearDown(self):
        # Restore original functions
        usb_sync.get_active_source_files = self.original_get_active_source_files
        usb_sync.scan_for_external_mounts = self.original_scan_for_external_mounts
        
        # Delete temp directories
        shutil.rmtree(self.src_dir)
        shutil.rmtree(self.usb_dir)

    def test_mount_auto_discovery_and_mirror_execution(self):
        # Execute mirror copy
        usb_sync.execute_external_usb_mirror()
        
        # Verify redundancy folder created
        redundancy_dir = os.path.join(self.usb_dir, "FuelSync_Local_Backups")
        self.assertTrue(os.path.exists(redundancy_dir))
        self.assertTrue(os.path.isdir(redundancy_dir))
        
        # Verify files are duplicated to flash drive
        db_dest = os.path.join(redundancy_dir, "pump_accounts.db")
        cache_dest = os.path.join(redundancy_dir, "corrections_cache.json")
        excel_dest = os.path.join(redundancy_dir, "ledger.xlsx")
        
        self.assertTrue(os.path.exists(db_dest))
        self.assertTrue(os.path.exists(cache_dest))
        self.assertTrue(os.path.exists(excel_dest))
        
        with open(db_dest, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "mock database contents")
            
        # Verify last sync info is updated
        status = usb_sync.get_last_sync_status()
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["event"], "usb_sync_complete")
        self.assertIn("pump_accounts.db", status["files"])

    def test_redundant_copy_prevention(self):
        # Run first time - should perform copy
        usb_sync.execute_external_usb_mirror()
        self.assertEqual(usb_sync.last_sync_info["status"], "complete")
        
        # Reset sync info status to check if it gets set to complete again
        usb_sync.last_sync_info["status"] = "idle"
        
        # Run second time - should not copy because files haven't changed
        usb_sync.execute_external_usb_mirror()
        self.assertEqual(usb_sync.last_sync_info["status"], "idle")
        
        # Modify a file to trigger copy
        with open(self.db_path, "w", encoding="utf-8") as f:
            f.write("updated database contents")
            
        # Run third time - should trigger copy again
        usb_sync.execute_external_usb_mirror()
        self.assertEqual(usb_sync.last_sync_info["status"], "complete")
        
        # Check that updated content is copied
        redundancy_dir = os.path.join(self.usb_dir, "FuelSync_Local_Backups")
        db_dest = os.path.join(redundancy_dir, "pump_accounts.db")
        with open(db_dest, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "updated database contents")

    def test_subscriber_notification(self):
        # Create a mock queue
        queue = MagicMock()
        usb_sync.register_subscriber(queue)
        
        # Set a mock main event loop
        loop = MagicMock()
        usb_sync.set_main_loop(loop)
        
        # Execute mirror copy
        usb_sync.execute_external_usb_mirror()
        
        # Verify notify_subscribers was called and event queued
        self.assertTrue(loop.call_soon_threadsafe.called)
        call_args = loop.call_soon_threadsafe.call_args[0]
        self.assertEqual(call_args[0], queue.put_nowait)
        event_payload = call_args[1]
        self.assertEqual(event_payload["status"], "success")
        self.assertEqual(event_payload["event"], "usb_sync_complete")

    @patch("platform.system")
    @patch("os.path.exists")
    @patch("os.listdir")
    def test_scan_for_external_mounts_platforms(self, mock_listdir, mock_exists, mock_system):
        # Test macOS mounting logic
        mock_system.return_value = "Darwin"
        mock_exists.side_effect = lambda path: path == "/Volumes"
        mock_listdir.return_value = ["USB_DRIVE", "INTERNAL_HD", "MY_EXTERNAL_BACKUP"]
        
        # Mock directory checks
        with patch("os.path.isdir", return_value=True):
            with patch("os.path.islink", return_value=False):
                res = self.original_scan_for_external_mounts()
                
        self.assertIn("/Volumes/USB_DRIVE", res)
        self.assertIn("/Volumes/MY_EXTERNAL_BACKUP", res)
        self.assertNotIn("/Volumes/INTERNAL_HD", res)  # Does not match any criteria tag

if __name__ == "__main__":
    unittest.main()
