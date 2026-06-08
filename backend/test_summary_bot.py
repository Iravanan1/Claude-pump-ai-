"""
Unit tests for summary_bot.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil
import json

import crypto_vault
import summary_bot

class TestSummaryBot(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Configure master key for cryptography
        os.environ["PUMP_AI_MASTER_KEY"] = "test_summary_secret_key"
        crypto_vault._fernet = None
        
        # Initialize tables
        conn = sqlite3.connect(self.test_db)
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
                paytm_transfers REAL,
                card_tender REAL,
                udhaar_sales REAL,
                expenses_amount REAL,
                validation_status TEXT,
                raw_data TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount TEXT,
                type TEXT,
                remarks TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_recon (
                date TEXT PRIMARY KEY,
                hsd_opening_dip_liters REAL DEFAULT 0.0,
                hsd_receipt_liters REAL DEFAULT 0.0,
                hsd_closing_dip_liters REAL DEFAULT 0.0,
                ms_opening_dip_liters REAL DEFAULT 0.0,
                ms_receipt_liters REAL DEFAULT 0.0,
                ms_closing_dip_liters REAL DEFAULT 0.0,
                actual_cash_deposited REAL DEFAULT 0.0,
                digital_wallet_settlements REAL DEFAULT 0.0,
                logged_udhaar_entries REAL DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dsm_shifts (
                date TEXT,
                shift_type TEXT,
                dsm_name TEXT,
                assigned_nozzles TEXT,
                cash_handed_over REAL,
                digital_slips_value REAL,
                calculated_shortage_or_excess REAL
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        crypto_vault._fernet = None

    def test_compile_whatsapp_sms_draft_empty(self):
        # When no data exists in database
        digest = summary_bot.compile_whatsapp_sms_draft("2026-06-15", db_path=self.test_db)
        expected = (
            "---\n"
            "*Daily Pump Summary: 2026-06-15*\n"
            "• HSD Sold: 0 Liters | MS Sold: 0 Liters\n"
            "• Total Cash Collected: ₹0\n"
            "• Digital Drops (Paytm/Cards): ₹0\n"
            "• Total Credit Sales (Udhaar): ₹0\n"
            "• Major Credit Parties: []\n"
            "• Shortages/Variance: ₹0\n"
            "---"
        )
        self.assertEqual(digest, expected)

    def test_compile_whatsapp_sms_draft_with_data(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Populate daily_summary
        cursor.execute("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales)
            VALUES ('2026-06-15', 500.5, 300.0, 85000.0, 15000.0)
        """)
        
        # Populate daily_ledger (encrypted raw_data mockup)
        cursor.execute("""
            INSERT INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, expenses_amount, validation_status, raw_data)
            VALUES ('2026-06-15', 800.5, 85000.0, 70000.0, 10000.0, 3000.0, 2000.0, 15000.0, 0.0, 'valid', '{}')
        """)
        
        # Populate ledger_entries (encrypted credit sales)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-15', ?, 'HR-55-1234', ?, 'udhaar', 'Credit sale')
        """, (crypto_vault.encrypt_field("Gopalram Ji Dhaba"), crypto_vault.encrypt_field(9000.0)))
        
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-15', ?, 'RJ-14-5678', ?, 'udhaar', 'Credit sale')
        """, (crypto_vault.encrypt_field("Sharma Transport"), crypto_vault.encrypt_field(6000.0)))
        
        # Populate stock_recon (cash short/over calculations)
        cursor.execute("""
            INSERT INTO stock_recon (date, actual_cash_deposited, digital_wallet_settlements, logged_udhaar_entries)
            VALUES ('2026-06-15', 69880.0, 15000.0, 15000.0)
        """)
        conn.commit()
        conn.close()
        
        # Compile draft and verify layout
        digest = summary_bot.compile_whatsapp_sms_draft("2026-06-15", db_path=self.test_db)
        
        # Expected outputs:
        # HSD Sold: 500.5, MS Sold: 300
        # Total Cash Collected: 69880 (actual_cash_deposited > 0)
        # Digital Drops: 15000 (digital_wallet_settlements > 0)
        # Total Credit Sales: 15000 (logged_udhaar_entries > 0)
        # Major Credit Parties: [Gopalram Ji Dhaba: ₹9000, Sharma Transport: ₹6000] (sorted descending)
        # Shortages/Variance:
        # calculated_sales_value = 85000
        # actual_reconciled_total = 69880 + 15000 + 15000 = 99880
        # cash_short_or_over = 99880 - 85000 = 14880 (overage)
        
        expected_lines = [
            "---",
            "*Daily Pump Summary: 2026-06-15*",
            "• HSD Sold: 500.5 Liters | MS Sold: 300 Liters",
            "• Total Cash Collected: ₹69880",
            "• Digital Drops (Paytm/Cards): ₹15000",
            "• Total Credit Sales (Udhaar): ₹15000",
            "• Major Credit Parties: [Gopalram Ji Dhaba: ₹9000, Sharma Transport: ₹6000]",
            "• Shortages/Variance: ₹14880",
            "---"
        ]
        
        expected = "\n".join(expected_lines)
        self.assertEqual(digest, expected)

if __name__ == "__main__":
    unittest.main()
