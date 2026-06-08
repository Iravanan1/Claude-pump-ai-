#!/usr/bin/env python3
"""
Unit and Integration Test Suite for the LogViewer Endpoints.
Verifies log streaming capacity limits, severity tag reads, and safe truncation processes.
"""

import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app

class TestLogViewerAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_log_path = os.path.abspath("test_pipeline.log")
        if os.path.exists(self.test_log_path):
            os.remove(self.test_log_path)
            
    def tearDown(self):
        if os.path.exists(self.test_log_path):
            os.remove(self.test_log_path)

    def test_logs_stream_and_clear_apis(self):
        """Verifies that logs/stream returns the last 100 lines and logs/clear truncates correctly."""
        # 1. Create a simulated log file with 120 lines (exceeding the 100-line cap)
        with open(self.test_log_path, "w", encoding="utf-8") as f:
            for i in range(120):
                f.write(f"2026-05-31 16:30:00 | INFO     | PumpAI | Line entry {i}\n")

        # 2. Patch logger.LOG_FILE dynamically to target our sandbox file
        with patch("logger.LOG_FILE", self.test_log_path):
             
            # A. Test GET /api/logs/stream
            response = self.client.get("/api/logs/stream")
            self.assertEqual(response.status_code, 200)
            
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(len(data["logs"]), 100) # Ensure it caps at exactly the last 100 lines
            self.assertEqual(data["logs"][0], "2026-05-31 16:30:00 | INFO     | PumpAI | Line entry 20")
            self.assertEqual(data["logs"][-1], "2026-05-31 16:30:00 | INFO     | PumpAI | Line entry 119")
            
            # B. Test POST /api/logs/clear
            clear_response = self.client.post("/api/logs/clear")
            self.assertEqual(clear_response.status_code, 200)
            
            clear_data = clear_response.json()
            self.assertEqual(clear_data["status"], "success")
            self.assertEqual(clear_data["message"], "Diagnostic history cleared successfully.")
            
            # C. Assert file is truncated (0 bytes size)
            self.assertTrue(os.path.exists(self.test_log_path))
            self.assertEqual(os.path.getsize(self.test_log_path), 0)
            
            # D. Test streaming again after clear - should return empty list
            empty_response = self.client.get("/api/logs/stream")
            self.assertEqual(empty_response.status_code, 200)
            self.assertEqual(empty_response.json()["logs"], [])

if __name__ == "__main__":
    unittest.main()
