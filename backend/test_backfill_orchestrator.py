#!/usr/bin/env python3
"""
Unit tests for backfill_orchestrator.py.
"""

import os
import sys
import unittest
import tempfile
import time
import shutil
from unittest.mock import patch, MagicMock

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from backfill_orchestrator import scan_and_sort_backlog, run_backfill_orchestration
from state_tracker import JobStatus

class TestBackfillOrchestrator(unittest.TestCase):
    
    def setUp(self):
        # Create temp folder for backlog
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        # Clean up temp folder
        shutil.rmtree(self.test_dir)
        
    def test_scan_and_sort_backlog_chronological(self):
        # Create three mock files with different creation times
        f1 = os.path.join(self.test_dir, "file_a.png")
        f2 = os.path.join(self.test_dir, "file_b.pdf")
        f3 = os.path.join(self.test_dir, "file_c.jpg")
        
        # Write dummy content
        for f in [f1, f2, f3]:
            with open(f, "w") as fd:
                fd.write("dummy")
                
        # Modify modification times to make file_c oldest, file_a middle, file_b newest
        now = time.time()
        os.utime(f3, (now - 100, now - 100)) # c is oldest
        os.utime(f1, (now - 50, now - 50))   # a is middle
        os.utime(f2, (now, now))             # b is newest
        
        sorted_files = scan_and_sort_backlog(self.test_dir)
        
        # Expected order: file_c.jpg, file_a.png, file_b.pdf
        self.assertEqual(len(sorted_files), 3)
        self.assertEqual(sorted_files[0], "file_c.jpg")
        self.assertEqual(sorted_files[1], "file_a.png")
        self.assertEqual(sorted_files[2], "file_b.pdf")

    @patch("backfill_orchestrator.calculate_file_hash", return_value="hash123")
    @patch("backfill_orchestrator.is_completed", return_value=True)
    @patch("backfill_orchestrator.upsert_job")
    def test_run_orchestration_skip_completed(self, mock_upsert, mock_completed, mock_hash):
        # Create one mock file
        f = os.path.join(self.test_dir, "file_a.png")
        with open(f, "w") as fd:
            fd.write("dummy")
            
        progress_events = list(run_backfill_orchestration(self.test_dir))
        
        self.assertEqual(len(progress_events), 1)
        self.assertIn("sheets processed successfully", progress_events[0])
        self.assertIn("Skipped completed", progress_events[0])
        
        # Verify state tracker wasn't requested to run preprocessing or optimization
        mock_upsert.assert_called_once()
        mock_completed.assert_called_once()

    @patch("backfill_orchestrator.calculate_file_hash", return_value="hash456")
    @patch("backfill_orchestrator.is_completed", return_value=False)
    @patch("backfill_orchestrator.upsert_job")
    @patch("backfill_orchestrator.mark_processing")
    @patch("backfill_orchestrator.optimize_register_image", return_value="/fake/opt.png")
    @patch("backfill_orchestrator.analyze_register_sheet")
    @patch("backfill_orchestrator.commit_to_ledger")
    @patch("backfill_orchestrator.mark_completed")
    def test_run_orchestration_cloud_success(
        self, mock_completed, mock_commit, mock_analyze, mock_opt,
        mock_processing, mock_upsert, mock_is_completed, mock_hash
    ):
        # Create one mock file
        f = os.path.join(self.test_dir, "file_a.png")
        with open(f, "w") as fd:
            fd.write("dummy")
            
        mock_analyze.return_value = {
            "date": "2026-06-01",
            "validation_status": "balanced",
            "mathematical_warnings": []
        }
        
        progress_events = list(run_backfill_orchestration(self.test_dir))
        
        self.assertEqual(len(progress_events), 1)
        self.assertIn("1/1 sheets processed successfully", progress_events[0])
        self.assertIn("Cloud API (Gemini/Claude)", progress_events[0])
        
        # Verify sequence
        mock_upsert.assert_called_once()
        mock_processing.assert_called_once()
        mock_opt.assert_called_once_with(f)
        mock_analyze.assert_called_once_with("/fake/opt.png", vision_engine="gemini", logic_engine="claude")
        mock_commit.assert_called_once()
        mock_completed.assert_called_once()

    @patch("backfill_orchestrator.calculate_file_hash", return_value="hash789")
    @patch("backfill_orchestrator.is_completed", return_value=False)
    @patch("backfill_orchestrator.upsert_job")
    @patch("backfill_orchestrator.mark_processing")
    @patch("backfill_orchestrator.optimize_register_image", return_value="/fake/opt.png")
    @patch("backfill_orchestrator.analyze_register_sheet")
    @patch("backfill_orchestrator.commit_to_ledger")
    @patch("backfill_orchestrator.mark_completed")
    def test_run_orchestration_cloud_fail_local_success(
        self, mock_completed, mock_commit, mock_analyze, mock_opt,
        mock_processing, mock_upsert, mock_is_completed, mock_hash
    ):
        # Create one mock file
        f = os.path.join(self.test_dir, "file_a.png")
        with open(f, "w") as fd:
            fd.write("dummy")
            
        # First call fails (Cloud API), second call succeeds (Local Ollama)
        mock_analyze.side_effect = [
            Exception("Cloud API connection rate limit or timeout!"),
            {
                "date": "2026-06-01",
                "validation_status": "balanced",
                "mathematical_warnings": []
            }
        ]
        
        progress_events = list(run_backfill_orchestration(self.test_dir))
        
        self.assertEqual(len(progress_events), 1)
        self.assertIn("1/1 sheets processed successfully", progress_events[0])
        self.assertIn("Local On-Device AI", progress_events[0])
        
        # Verify analyze_register_sheet called twice: once with cloud, once with local
        self.assertEqual(mock_analyze.call_count, 2)
        mock_analyze.assert_any_call("/fake/opt.png", vision_engine="gemini", logic_engine="claude")
        mock_analyze.assert_any_call("/fake/opt.png", vision_engine="local", logic_engine="local")
        
        mock_commit.assert_called_once()
        mock_completed.assert_called_once()

    @patch("backfill_orchestrator.calculate_file_hash", return_value="hash999")
    @patch("backfill_orchestrator.is_completed", return_value=False)
    @patch("backfill_orchestrator.upsert_job")
    @patch("backfill_orchestrator.mark_processing")
    @patch("backfill_orchestrator.optimize_register_image", return_value="/fake/opt.png")
    @patch("backfill_orchestrator.analyze_register_sheet")
    @patch("backfill_orchestrator.mark_failed")
    def test_run_orchestration_all_failed(
        self, mock_failed, mock_analyze, mock_opt,
        mock_processing, mock_upsert, mock_is_completed, mock_hash
    ):
        # Create one mock file
        f = os.path.join(self.test_dir, "file_a.png")
        with open(f, "w") as fd:
            fd.write("dummy")
            
        # Both calls fail
        mock_analyze.side_effect = Exception("General extraction error")
        
        progress_events = list(run_backfill_orchestration(self.test_dir))
        
        self.assertEqual(len(progress_events), 1)
        self.assertIn("Failed (file_a.png)", progress_events[0])
        
        # Verify mark_failed called
        mock_failed.assert_called_once()

if __name__ == "__main__":
    unittest.main()
