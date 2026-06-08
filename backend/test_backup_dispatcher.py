#!/usr/bin/env python3
"""
Unit tests for backup_dispatcher.py
"""

import os
import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from backup_dispatcher import (
    init_queue_db,
    queue_backup,
    fetch_metrics_summary,
    dispatch_daily_ledger_backup,
    dispatch_daily_ledger_backup_background,
    retry_pending_backups
)

class TestBackupDispatcher(unittest.TestCase):
    
    def setUp(self):
        self.db_path = "test_backup_dispatcher.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
            
        # Initialize test tables
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_hsd_liters REAL DEFAULT 0.0,
            total_ms_liters REAL DEFAULT 0.0,
            total_cash_calculated REAL DEFAULT 0.0,
            total_credit_sales REAL DEFAULT 0.0,
            total_testing_deductions REAL DEFAULT 0.0,
            is_verified INTEGER DEFAULT 0
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ledger (
            date TEXT PRIMARY KEY,
            total_sales_liters REAL,
            total_amount_inr REAL,
            cash_tender REAL,
            upi_tender REAL,
            card_tender REAL,
            udhaar_sales REAL,
            expenses_amount REAL,
            validation_status TEXT,
            raw_data TEXT
        )
        """)
        conn.commit()
        conn.close()
        
        # Initialize queue table
        init_queue_db(self.db_path)
        
        # Create a mock Excel file path for testing
        workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.mock_excel_path = os.path.join(workspace_dir, "mock_Pump_Accounts.xlsx")
        with open(self.mock_excel_path, "w") as f:
            f.write("mock content")
            
        self.log_path = "logs/pipeline.log"
        if os.path.exists(self.log_path):
            try:
                os.remove(self.log_path)
            except Exception:
                pass

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.mock_excel_path):
            os.remove(self.mock_excel_path)
        if os.path.exists(self.log_path):
            try:
                os.remove(self.log_path)
            except Exception:
                pass

    def test_queue_backup_flow(self):
        queue_backup("2026-06-01", self.db_path)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT date_string, status, retry_count FROM backup_queue")
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "2026-06-01")
        self.assertEqual(row[1], "PENDING")
        self.assertEqual(row[2], 0)

    def test_fetch_metrics_summary(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified)
            VALUES ('2026-06-01', 1200.0, 1800.0, 250000.0, 4500.0, 10.0, 1)
        """)
        cursor.execute("""
            INSERT INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, card_tender, udhaar_sales, expenses_amount, validation_status)
            VALUES ('2026-06-01', 3000.0, 250000.0, 240000.0, 0.0, 0.0, 4500.0, 5500.0, 'valid')
        """)
        conn.commit()
        conn.close()
        
        summary = fetch_metrics_summary("2026-06-01", self.db_path)
        self.assertIn("Daily Ledger Summary for 2026-06-01:", summary)
        self.assertIn("Fuel HSD Sales: 1200.00 L", summary)
        self.assertIn("Fuel MS Sales: 1800.00 L", summary)
        self.assertIn("Calculated Cash: 250000.00 INR", summary)
        self.assertIn("Credit Sales: 4500.00 INR", summary)
        self.assertIn("Testing Deductions: 10.00 L", summary)
        self.assertIn("Verification Status: VERIFIED", summary)
        self.assertIn("Cash Tender: 240000.00 INR", summary)
        self.assertIn("Expenses: 5500.00 INR", summary)

    @patch("smtplib.SMTP")
    @patch("requests.post")
    @patch("os.path.exists")
    @patch.dict(os.environ, {
        "SMTP_SERVER": "smtp.mock.com",
        "SMTP_PORT": "587",
        "SENDER_EMAIL": "sender@mock.com",
        "RECEIVER_EMAIL": "receiver@mock.com",
        "TELEGRAM_BOT_TOKEN": "mock_token",
        "TELEGRAM_CHAT_ID": "mock_chat_id",
        "EXPORT_EXCEL_PATH": "mock_Pump_Accounts.xlsx"
    })
    def test_dispatch_backup_success(self, mock_exists, mock_post, mock_smtp):
        mock_exists.return_value = True
        
        # Mock SMTP connection and send
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        
        # Mock Telegram response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        # Add a pending queue entry
        queue_backup("2026-06-01", self.db_path)
        
        res = dispatch_daily_ledger_backup("2026-06-01", self.db_path)
        self.assertTrue(res)
        
        # Check SMTP was called
        mock_smtp.assert_called_once_with("smtp.mock.com", 587, timeout=10)
        mock_server.starttls.assert_called_once()
        mock_server.send_message.assert_called_once()
        
        # Check Telegram POST was called
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.telegram.org/botmock_token/sendDocument")
        self.assertEqual(kwargs["data"]["chat_id"], "mock_chat_id")
        self.assertIn("document", kwargs["files"])
        
        # Queue task should be deleted on success
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM backup_queue WHERE date_string = '2026-06-01'")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    @patch("smtplib.SMTP")
    @patch("requests.post")
    @patch("os.path.exists")
    @patch.dict(os.environ, {
        "SMTP_SERVER": "smtp.mock.com",
        "SMTP_PORT": "587",
        "SENDER_EMAIL": "sender@mock.com",
        "RECEIVER_EMAIL": "receiver@mock.com",
        "TELEGRAM_BOT_TOKEN": "mock_token",
        "TELEGRAM_CHAT_ID": "mock_chat_id",
        "EXPORT_EXCEL_PATH": "mock_Pump_Accounts.xlsx"
    })
    def test_dispatch_backup_traps_exception_quietly(self, mock_exists, mock_post, mock_smtp):
        mock_exists.return_value = True
        
        # Make SMTP raise an exception
        mock_smtp.side_effect = ConnectionRefusedError("Connection refused by mock SMTP")
        
        # Add a pending queue entry
        queue_backup("2026-06-02", self.db_path)
        
        # Execute - should return False, but NOT raise an exception
        res = dispatch_daily_ledger_backup("2026-06-02", self.db_path)
        self.assertFalse(res)
        
        # Verify queue entry status is PENDING and retry count has incremented to 1
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status, retry_count FROM backup_queue WHERE date_string = '2026-06-02'")
        row = cursor.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "PENDING")
        self.assertEqual(row[1], 1)
        
        # Check warning was logged in pipeline.log
        pipeline_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pipeline.log")
        self.assertTrue(os.path.exists(pipeline_log_path))
        with open(pipeline_log_path, "r") as f:
            log_content = f.read()
        self.assertIn("Backup dispatch failed for date 2026-06-02", log_content)
        self.assertIn("Connection refused by mock SMTP", log_content)

    @patch("backup_dispatcher.dispatch_daily_ledger_backup")
    def test_retry_pending_backups(self, mock_dispatch):
        queue_backup("2026-06-03", self.db_path)
        queue_backup("2026-06-04", self.db_path)
        
        # Simulate completing 2026-06-03 so it's not pending anymore
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE backup_queue SET status = 'COMPLETED' WHERE date_string = '2026-06-03'")
        conn.commit()
        conn.close()
        
        # Run retry_pending_backups
        retry_pending_backups(self.db_path)
        
        # Should only dispatch the pending one ("2026-06-04")
        mock_dispatch.assert_called_once_with("2026-06-04", self.db_path)

if __name__ == "__main__":
    unittest.main()
