import os
import sys
import zipfile
import shutil
import unittest
from datetime import datetime, timedelta

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import backup

class TestBackupSystem(unittest.TestCase):
    def setUp(self):
        # Setup temporary directories and files
        self.original_backups_dir = backup.BACKUPS_DIR
        self.test_backups_dir = os.path.join(BACKEND_DIR, "test_backups")
        backup.BACKUPS_DIR = self.test_backups_dir
        
        if os.path.exists(self.test_backups_dir):
            shutil.rmtree(self.test_backups_dir)
            
        os.makedirs(self.test_backups_dir, exist_ok=True)
        
        # Create mock database and Excel files in temporary directory
        self.test_db_file = os.path.join(self.test_backups_dir, "ledger.db")
        self.test_xlsx_file = os.path.join(self.test_backups_dir, "ledger.xlsx")
        
        # Ensure they exist (write mock bytes)
        with open(self.test_db_file, "w") as f:
            f.write("mock_db_data")
        with open(self.test_xlsx_file, "w") as f:
            f.write("mock_excel_data")
            
        # Patch FILES_TO_BACKUP
        self.original_files_to_backup = backup.FILES_TO_BACKUP
        backup.FILES_TO_BACKUP = [
            self.test_db_file,
            self.test_xlsx_file
        ]

    def tearDown(self):
        # Restore backups path and files to backup
        backup.BACKUPS_DIR = self.original_backups_dir
        backup.FILES_TO_BACKUP = self.original_files_to_backup
        
        # Clean up temporary test backups directory
        if os.path.exists(self.test_backups_dir):
            shutil.rmtree(self.test_backups_dir)

    def test_execute_local_backup_creates_zip(self):
        """Verifies that execute_local_backup successfully packs existing assets into a zip file."""
        zip_path = backup.execute_local_backup()
        self.assertIsNotNone(zip_path)
        self.assertTrue(os.path.exists(zip_path))
        self.assertTrue(zip_path.endswith(".zip"))
        
        # Verify contents of zip
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            namelist = zipf.namelist()
            self.assertIn("ledger.db", namelist)
            self.assertIn("ledger.xlsx", namelist)

    def test_rolling_retention_policy_deletes_old_files(self):
        """Verifies that rolling 30-day retention prunes backups older than 30 days but keeps newer ones."""
        # Create a newer backup (today)
        zip_path_new = backup.execute_local_backup()
        new_filename = os.path.basename(zip_path_new)
        
        # Create an old mock backup file (e.g., dated 40 days ago)
        old_timestamp = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d_%H%M%S")
        old_filename = f"backup_{old_timestamp}.zip"
        old_zip_path = os.path.join(self.test_backups_dir, old_filename)
        
        with open(old_zip_path, "w") as f:
            f.write("fake_zip_bytes")
            
        self.assertTrue(os.path.exists(old_zip_path))
        
        # Run rolling retention pruning
        backup.apply_rolling_retention()
        
        # Old backup should be deleted
        self.assertFalse(os.path.exists(old_zip_path))
        
        # New backup should still exist
        self.assertTrue(os.path.exists(zip_path_new))

if __name__ == "__main__":
    unittest.main()
