#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import price_gap_filler
import price_registry
from crypto_vault import encrypt_raw_data, decrypt_raw_data


class TestPriceGapFiller(unittest.TestCase):
    def setUp(self):
        self.original_db = price_gap_filler.DB_PATH
        self.test_db = os.path.join(BACKEND_DIR, "test_rates_filler.db")
        price_gap_filler.DB_PATH = self.test_db
        price_registry.DB_PATH = self.test_db
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
            
        # Initialize SQLite database schema
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fuel_rates (
            date TEXT PRIMARY KEY,
            hsd_rate REAL NOT NULL,
            ms_rate REAL NOT NULL,
            premium_hsd_rate REAL DEFAULT NULL,
            premium_ms_rate REAL DEFAULT NULL
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_hsd_liters REAL DEFAULT 0.0,
            total_ms_liters REAL DEFAULT 0.0,
            total_cash_calculated REAL DEFAULT 0.0,
            total_credit_sales REAL DEFAULT 0.0,
            total_testing_deductions REAL DEFAULT 0.0,
            is_verified INTEGER DEFAULT 0,
            meter_replaced INTEGER DEFAULT 0,
            replacement_offset_liters REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            total_sales_liters REAL,
            total_amount_inr REAL,
            cash_tender REAL,
            upi_tender REAL,
            paytm_transfers REAL,
            card_tender REAL,
            udhaar_sales REAL,
            expenses_amount REAL,
            validation_status TEXT,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        price_gap_filler.DB_PATH = self.original_db
        price_registry.DB_PATH = self.original_db
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_resolve_missing_fuel_price(self):
        """Verifies lookup scans backwards chronologically."""
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-06-01", 90.0, 100.0))
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-06-03", 92.0, 102.0))
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-06-04", 93.0, 103.0))
        conn.commit()
        conn.close()

        # Check lookback for target date '2026-06-05' (closest should be 2026-06-04)
        rate, date = price_gap_filler.resolve_missing_fuel_price("2026-06-05", "MS")
        self.assertEqual(rate, 103.0)
        self.assertEqual(date, "2026-06-04")

        # Check lookback for target date '2026-06-04' (preceding should be 2026-06-03)
        rate, date = price_gap_filler.resolve_missing_fuel_price("2026-06-04", "HSD")
        self.assertEqual(rate, 92.0)
        self.assertEqual(date, "2026-06-03")

        # Check lookback for target date '2026-06-02' (preceding should be 2026-06-01)
        rate, date = price_gap_filler.resolve_missing_fuel_price("2026-06-02", "MS")
        self.assertEqual(rate, 100.0)
        self.assertEqual(date, "2026-06-01")

        # Check lookback with no preceding entries (should be None)
        rate, date = price_gap_filler.resolve_missing_fuel_price("2026-06-01", "MS")
        self.assertIsNone(rate)
        self.assertIsNone(date)

    @patch("exporter.export_db_to_excel")
    @patch("exporter.generate_accounting_export")
    def test_recalculate_ledger_revenue_by_rate(self, mock_generate, mock_export):
        """Verifies retroactive revenue correction updates SQLite and calls exporter."""
        date_str = "2026-06-05"
        
        # 1. Seed fuel_rates
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", (date_str, 90.0, 100.0))
        
        # 2. Seed daily_summary
        cursor.execute("INSERT INTO daily_summary (date, total_cash_calculated) VALUES (?, ?)", (date_str, 5000.0))
        
        # 3. Seed daily_ledger with encrypted raw_data
        raw_data = {
            "date": date_str,
            "nozzles": [
                {
                    "nozzle_name": "MS-1",
                    "fuel_type": "REGULAR_MS",
                    "net_sales_liters": 20.0,
                    "rate": 100.0,
                    "amount_calculated": 2000.0
                },
                {
                    "nozzle_name": "HSD-1",
                    "fuel_type": "REGULAR_HSD",
                    "net_sales_liters": 30.0,
                    "rate": 90.0,
                    "amount_calculated": 2700.0
                }
            ],
            "total_amount_inr": 4700.0,
            "cash_tender": 4700.0,
            "udhaar_sales": 0.0,
            "expenses_amount": 0.0
        }
        encrypted = encrypt_raw_data(raw_data)
        cursor.execute("""
            INSERT INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, udhaar_sales, expenses_amount, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date_str, 50.0, 4700.0, 4700.0, 0.0, 0.0, json.dumps(encrypted)))
        conn.commit()
        conn.close()

        # Recalculate MS rate from 100 to 110
        success = price_gap_filler.recalculate_ledger_revenue_by_rate(date_str, "MS", 110.0)
        self.assertTrue(success)

        # Check database was updated
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Check fuel_rates
        cursor.execute("SELECT ms_rate FROM fuel_rates WHERE date = ?", (date_str,))
        self.assertEqual(cursor.fetchone()[0], 110.0)
        
        # Check daily_summary
        cursor.execute("SELECT total_cash_calculated FROM daily_summary WHERE date = ?", (date_str,))
        # MS: 20 * 110 = 2200. HSD: 30 * 90 = 2700. Total = 4900
        self.assertEqual(cursor.fetchone()[0], 4900.0)
        
        # Check daily_ledger
        cursor.execute("SELECT total_amount_inr, cash_tender, raw_data FROM daily_ledger WHERE date = ?", (date_str,))
        row = cursor.fetchone()
        self.assertEqual(row[0], 4900.0)
        self.assertEqual(row[1], 4900.0)
        
        decrypted = decrypt_raw_data(json.loads(row[2]))
        self.assertEqual(decrypted["total_amount_inr"], 4900.0)
        self.assertEqual(decrypted["nozzles"][0]["rate"], 110.0)
        self.assertEqual(decrypted["nozzles"][0]["amount_calculated"], 2200.0)
        
        conn.close()
        
        # Verify exporter calls
        mock_export.assert_called_once()
        mock_generate.assert_called_once_with(date_str)


if __name__ == "__main__":
    unittest.main()
