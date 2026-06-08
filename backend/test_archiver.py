import os
import sys
import shutil
import unittest

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import archiver

class TestArchiver(unittest.TestCase):
    def setUp(self):
        self.original_workspace = os.path.dirname(BACKEND_DIR)
        
        # Setup temporary raw workspace folder for testing
        self.test_root = os.path.join(BACKEND_DIR, "test_ledger_photos")
        self.temp_raw = os.path.join(self.test_root, "raw_unprocessed")
        os.makedirs(self.temp_raw, exist_ok=True)

    def tearDown(self):
        # Clean up files in workspace ledger_photos created during run
        ledger_photos_path = os.path.join(self.original_workspace, "ledger_photos")
        if os.path.exists(ledger_photos_path):
            shutil.rmtree(ledger_photos_path)
            
        if os.path.exists(self.test_root):
            shutil.rmtree(self.test_root)

    def test_archive_success_balanced(self):
        """Verifies that a balanced status moves the image to successfully_imported with the date prefix."""
        test_file = os.path.join(self.temp_raw, "register_99.png")
        with open(test_file, "w") as f:
            f.write("mock_binary_data")
            
        dest = archiver.archive_processed_file(test_file, "balanced", "2026-06-20")
        
        self.assertTrue(os.path.exists(dest))
        self.assertIn("successfully_imported", dest)
        self.assertIn("2026-06-20_register_99.png", dest)
        self.assertFalse(os.path.exists(test_file))

    def test_archive_requires_review(self):
        """Verifies that a discrepant status moves the image to requires_human_review."""
        test_file = os.path.join(self.temp_raw, "register_101.png")
        with open(test_file, "w") as f:
            f.write("mock_binary_data_error")
            
        dest = archiver.archive_processed_file(test_file, "math_discrepancy", "2026-06-21")
        
        self.assertTrue(os.path.exists(dest))
        self.assertIn("requires_human_review", dest)
        self.assertIn("2026-06-21_review_register_101.png", dest)
        self.assertFalse(os.path.exists(test_file))

    def test_archive_duplicate_collision(self):
        """Verifies that duplicate filename collisions are solved by appending sequential suffixes."""
        test_file_1 = os.path.join(self.temp_raw, "register_duplicate.png")
        with open(test_file_1, "w") as f:
            f.write("data_1")
            
        # Archive once
        dest_1 = archiver.archive_processed_file(test_file_1, "balanced", "2026-06-22")
        
        # Create second file with same name
        test_file_2 = os.path.join(self.temp_raw, "register_duplicate.png")
        with open(test_file_2, "w") as f:
            f.write("data_2")
            
        # Archive second time
        dest_2 = archiver.archive_processed_file(test_file_2, "balanced", "2026-06-22")
        
        self.assertTrue(os.path.exists(dest_1))
        self.assertTrue(os.path.exists(dest_2))
        self.assertNotEqual(dest_1, dest_2)
        self.assertIn("register_duplicate_1.png", dest_2)

if __name__ == "__main__":
    unittest.main()
