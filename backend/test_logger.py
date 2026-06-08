import os
import sys
import unittest
import logging

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import logger

class TestPipelineLogger(unittest.TestCase):
    def setUp(self):
        log_file = logger.LOG_FILE
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        for handler in logger.logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.close()
                try:
                    handler.stream = handler._open()
                except Exception:
                    pass

    def test_logger_setup(self):
        """Verifies that unified logger has handlers configured correctly."""
        self.assertIsNotNone(logger.logger)
        handlers = logger.logger.handlers
        
        # Verify we have at least console and rotating file handlers
        self.assertTrue(len(handlers) >= 2)
        
        has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logger.RotatingFileHandler) for h in handlers)
        has_file = any(isinstance(h, logger.RotatingFileHandler) for h in handlers)
        
        self.assertTrue(has_console)
        self.assertTrue(has_file)

    def test_log_pipeline_transaction(self):
        """Verifies that log_pipeline_transaction writes structured records to logs/pipeline.log file."""
        log_file = logger.LOG_FILE
        
        # Call logging utility
        logger.log_pipeline_transaction(
            filename="test_pipeline_run.png",
            execution_time=1.456,
            token_usage=680,
            math_passed=True
        )
        
        self.assertTrue(os.path.exists(log_file))
        
        # Verify contents in file
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("TRANSACTION RECORD", content)
            self.assertIn("File: test_pipeline_run.png", content)
            self.assertIn("ExecTime: 1.456s", content)
            self.assertIn("Tokens: 680", content)
            self.assertIn("MathAudit: PASS", content)

    def test_log_pipeline_transaction_failure(self):
        """Verifies transaction logging on exception dropouts."""
        log_file = logger.LOG_FILE
        
        logger.log_pipeline_transaction(
            filename="failing_sheet.png",
            execution_time=0.890,
            token_usage=0,
            math_passed=False,
            exception_trace="ConnectError: Cloud connection timed out."
        )
        
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("TRANSACTION RECORD", content)
            self.assertIn("File: failing_sheet.png", content)
            self.assertIn("MathAudit: FAIL", content)
            self.assertIn("ExceptionTrace: ConnectError: Cloud connection timed out.", content)

if __name__ == "__main__":
    unittest.main()
