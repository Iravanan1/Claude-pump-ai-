#!/usr/bin/env python3
"""
Unit Test Suite for Image Clarity Validation Guardrail.
Tests:
1. Blur Detection (Laplacian focus metric calculations for sharp and blurred images).
2. Blank Page Filtering (Intensity histogram / contrast variance for blank images).
3. Sandbox Database State Machine Transitions (Transitioning status to FAILED_PREPROCESSING).
4. Safe Asset Routing (Relocating bad-quality files directly intorequires_human_review directory).
"""

import os
import sys
import shutil
import sqlite3
import unittest
import cv2
import numpy as np

# Resolve path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import init_db
import state_tracker
from image_guard import validate_image_clarity
from archiver import archive_processed_file

TEST_DB_PATH = os.path.join(BACKEND_DIR, "test_ledger_guard.db")
TEST_SHARP_IMG = os.path.join(BACKEND_DIR, "test_sharp.png")
TEST_BLUR_IMG = os.path.join(BACKEND_DIR, "test_blurry.png")
TEST_BLANK_IMG = os.path.join(BACKEND_DIR, "test_blank.png")

# Dynamic DB Path override
state_tracker.DB_PATH = TEST_DB_PATH
init_db.DB_PATH = TEST_DB_PATH


class TestImageGuard(unittest.TestCase):
    def setUp(self):
        """Sets up isolated databases, temporary paths, and creates sharp, blurry, and blank mock registers."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            
        init_db.initialize_database()
        state_tracker.init_state_db(TEST_DB_PATH)
        
        # 1. Create a Sharp high-contrast mock register (black grids and large text on white background)
        img_sharp = np.ones((400, 400, 3), dtype=np.uint8) * 255
        # Draw high-contrast sharp gridlines and text
        cv2.rectangle(img_sharp, (20, 20), (380, 380), (0, 0, 0), 4)
        for i in range(50, 350, 50):
            cv2.line(img_sharp, (20, i), (380, i), (0, 0, 0), 2)
            cv2.line(img_sharp, (i, 20), (i, 380), (0, 0, 0), 2)
        cv2.putText(img_sharp, "REG", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
        cv2.imwrite(TEST_SHARP_IMG, img_sharp)
        
        # 2. Create a Blurry mock register by applying heavy Gaussian blur to the sharp one
        img_blurry = cv2.GaussianBlur(img_sharp, (31, 31), 0)
        cv2.imwrite(TEST_BLUR_IMG, img_blurry)
        
        # 3. Create a Blank solid white/yellow mock register
        img_blank = np.ones((400, 400, 3), dtype=np.uint8) * 253  # Uniform light gray/white page
        # Add very minor scanner sensor noise
        noise = np.random.normal(0, 0.5, img_blank.shape).astype(np.uint8)
        img_blank = cv2.add(img_blank, noise)
        cv2.imwrite(TEST_BLANK_IMG, img_blank)
        
        self.test_files = [TEST_SHARP_IMG, TEST_BLUR_IMG, TEST_BLANK_IMG]

    def tearDown(self):
        """Removes mock images, databases, and prunes review/unprocessed directories."""
        for f in self.test_files:
            if os.path.exists(f):
                os.remove(f)
                
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            
        # Prune photos directories created by archiver
        workspace_dir = os.path.dirname(BACKEND_DIR)
        photos_dir = os.path.join(workspace_dir, "ledger_photos")
        if os.path.exists(photos_dir):
            shutil.rmtree(photos_dir)

    def test_sharp_image_passes(self):
        """Verifies that a sharp high-contrast mock register successfully passes image validation checks."""
        res = validate_image_clarity(TEST_SHARP_IMG, blur_threshold=100.0, contrast_threshold=20.0)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "OK")
        self.assertGreaterEqual(res["focus_score"], 100.0)
        self.assertGreaterEqual(res["contrast_score"], 20.0)

    def test_blurry_image_fails_focus(self):
        """Verifies that a blurred mock register fails the focus checks (Laplacian focus score < 100)."""
        res = validate_image_clarity(TEST_BLUR_IMG, blur_threshold=100.0, contrast_threshold=20.0)
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "BLURRY")
        self.assertLess(res["focus_score"], 100.0)

    def test_blank_image_fails_contrast(self):
        """Verifies that a completely blank mock register fails the blank page check (contrast variance < 20)."""
        res = validate_image_clarity(TEST_BLANK_IMG, blur_threshold=100.0, contrast_threshold=20.0)
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "BLANK")
        self.assertLess(res["contrast_score"], 20.0)

    def test_state_machine_transition_preprocessing_failed(self):
        """Verifies that mark_preprocessing_failed correctly transitions and logs jobs to FAILED_PREPROCESSING in batch_status."""
        file_hash = "fake_hash_guard_123"
        filename = "test_guard.png"
        
        # Upsert pending
        state_tracker.upsert_job(file_hash, filename, db_path=TEST_DB_PATH)
        self.assertEqual(state_tracker.get_job_status(file_hash, db_path=TEST_DB_PATH), "PENDING")
        
        # Transition preprocessing failed
        reason = "Focus check failed: BLURRY focus variance = 23.45"
        state_tracker.mark_preprocessing_failed(file_hash, reason, db_path=TEST_DB_PATH)
        
        # Verify status
        status = state_tracker.get_job_status(file_hash, db_path=TEST_DB_PATH)
        self.assertEqual(status, "FAILED_PREPROCESSING")
        
        # Verify last error is saved
        conn = sqlite3.connect(TEST_DB_PATH)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash = ?", (file_hash,)).fetchone()
        conn.close()
        self.assertEqual(row[0], reason)

    def test_asset_routing_requires_review(self):
        """Verifies that files with FAILED_PREPROCESSING status are routed directly to REQUIRES_REVIEW."""
        # Create a quick mock file
        temp_file = os.path.join(BACKEND_DIR, "temp_review_test.png")
        shutil.copy(TEST_BLUR_IMG, temp_file)
        self.assertTrue(os.path.exists(temp_file))
        
        # Call archiver
        archived_path = archive_processed_file(temp_file, status="FAILED_PREPROCESSING")
        self.assertFalse(os.path.exists(temp_file))
        self.assertTrue(os.path.exists(archived_path))
        
        # Path details checks
        self.assertIn("requires_human_review", archived_path)
        self.assertIn("review_temp_review_test.png", os.path.basename(archived_path))


if __name__ == "__main__":
    unittest.main()
