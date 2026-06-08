#!/usr/bin/env python3
"""
Unit and Integration Test Suite for the Nozzle Testing and RTS Tracker.
"""

import os
import sys
import sqlite3
import unittest
from fastapi.testclient import TestClient

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import testing_rts
import reconciliation
import price_registry
from main import app

class TestTestingRTS(unittest.TestCase):
    def setUp(self):
        # Override DB paths to point to a test sandbox database
        self.original_rts_db = testing_rts.DB_PATH
        self.original_recon_db = reconciliation.DB_PATH
        self.original_pr_db = price_registry.DB_PATH
        
        self.test_db = os.path.join(BACKEND_DIR, "test_testing_rts.db")
        testing_rts.DB_PATH = self.test_db
        reconciliation.DB_PATH = self.test_db
        price_registry.DB_PATH = self.test_db
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
            
        # 1. Initialize Tables in the sandbox
        testing_rts.init_testing_rts_db(self.test_db)
        reconciliation.init_recon_db(self.test_db)
        price_registry.init_rates_db()
        
        # Initialize ledger_entries, daily_summary, and daily_ledger tables
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
            is_verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ledger (
            date TEXT PRIMARY KEY,
            cash_tender REAL DEFAULT 0.0,
            upi_tender REAL DEFAULT 0.0,
            paytm_transfers REAL DEFAULT 0.0,
            card_tender REAL DEFAULT 0.0,
            udhaar_sales REAL DEFAULT 0.0,
            raw_data TEXT
        )
        """)
        conn.commit()
        conn.close()
        
        self.client = TestClient(app)

    def tearDown(self):
        # Restore DB paths
        testing_rts.DB_PATH = self.original_rts_db
        reconciliation.DB_PATH = self.original_recon_db
        price_registry.DB_PATH = self.original_pr_db
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_database_init(self):
        """Verifies that nozzle_testing_logs table is correctly created."""
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nozzle_testing_logs'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_record_nozzle_testing_validation(self):
        """Verifies validation logic in record_nozzle_testing."""
        # 1. Invalid product type
        with self.assertRaises(ValueError):
            testing_rts.record_nozzle_testing("2026-06-01", "MS-1", "INVALID", 5.0, True, self.test_db)
            
        # 2. Negative/zero volumes
        with self.assertRaises(ValueError):
            testing_rts.record_nozzle_testing("2026-06-01", "MS-1", "MS", -5.0, True, self.test_db)
        with self.assertRaises(ValueError):
            testing_rts.record_nozzle_testing("2026-06-01", "MS-1", "MS", 0, True, self.test_db)

    def test_record_nozzle_testing_success(self):
        """Verifies recording nozzle testing calibration draws successfully."""
        res_ms = testing_rts.record_nozzle_testing(
            "2026-06-01", "MS-1", "MS", 5.0, True, self.test_db
        )
        self.assertEqual(res_ms["status"], "success")
        self.assertEqual(res_ms["testing_volume_liters"], 5.0)
        self.assertTrue(res_ms["rts_verified"])
        
        # Verify row in database
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT nozzle_id, product_type, testing_volume_liters, rts_verified FROM nozzle_testing_logs")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "MS-1")
        self.assertEqual(row[1], "MS")
        self.assertEqual(row[2], 5.0)
        self.assertEqual(row[3], 1)
        conn.close()

    def test_reconciliation_math_interception(self):
        """Verifies stock returns additions and billable cash calculations in reconciliation."""
        # 1. Setup mock rates
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-06-01", 90.00, 100.00))
        
        # 2. Setup mock daily summary totalizer gross flows
        # Gross flows: 1000L HSD, 500L MS
        cursor.execute("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated)
            VALUES ('2026-06-01', 1000.0, 500.0, 140000.0)
        """)
        
        # 3. Setup mock dip stocks
        # Expected stock = Opening + Receipt - Gross Sales + RTS = 5000 + 2000 - 1000 + 10 = 6010 (for HSD)
        cursor.execute("""
            INSERT INTO stock_recon (
                date, hsd_opening_dip_liters, hsd_receipt_liters, hsd_closing_dip_liters,
                ms_opening_dip_liters, ms_receipt_liters, ms_closing_dip_liters
            ) VALUES ('2026-06-01', 5000.0, 2000.0, 6010.0, 4000.0, 1000.0, 4505.0)
        """)
        conn.commit()
        conn.close()
        
        # 4. Record nozzle testing draws: 10L HSD (RTS verified), 5L MS (RTS verified)
        testing_rts.record_nozzle_testing("2026-06-01", "HSD-1", "HSD", 10.0, True, self.test_db)
        testing_rts.record_nozzle_testing("2026-06-01", "MS-1", "MS", 5.0, True, self.test_db)
        
        # 5. Run calculate_daily_variance
        res = reconciliation.calculate_daily_variance("2026-06-01", db_path=self.test_db)
        
        # A. Stock RTS check: Expected Stock should add back testing volumes
        # Expected HSD Stock = 5000 + 2000 - 1000 + 10 = 6010L
        # Expected MS Stock = 4000 + 1000 - 500 + 5 = 4505L
        self.assertEqual(res["expected_hsd_book_stock"], 6010.0)
        self.assertEqual(res["expected_ms_book_stock"], 4505.0)
        
        # Variance should be 0.0 for both
        self.assertEqual(res["hsd_variance_liters"], 0.0)
        self.assertEqual(res["ms_variance_liters"], 0.0)
        
        # B. Billable Liters check:
        # HSD Billable = 1000 - 10 = 990L
        # MS Billable = 500 - 5 = 495L
        self.assertEqual(res["hsd_billable_liters"], 990.0)
        self.assertEqual(res["ms_billable_liters"], 495.0)
        
        # C. Expected Revenue check:
        # HSD expected revenue = 990 * 90.00 = ₹89,100
        # MS expected revenue = 495 * 100.00 = ₹49,500
        # Total calculated billable revenue = 89100 + 49500 = ₹138,600
        self.assertEqual(res["expected_hsd_revenue"], 89100.0)
        self.assertEqual(res["expected_ms_revenue"], 49500.0)
        self.assertEqual(res["calculated_sales_value"], 138600.0)

    def test_api_endpoints(self):
        """Verifies FastAPI controller endpoints POST and GET for nozzle testing draws."""
        # Clean test DB by swapping target path in main.py
        import main
        original_main_db = main.DB_PATH
        main.DB_PATH = self.test_db
        
        try:
            # 1. Test POST endpoint
            payload = {
                "date": "2026-06-01",
                "nozzle_id": "HSD-2",
                "product_type": "HSD",
                "testing_volume_liters": 5.0,
                "rts_verified": True
            }
            response = self.client.post("/api/nozzle-testing", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["nozzle_id"], "HSD-2")
            
            # 2. Test GET endpoint
            response_get = self.client.get("/api/nozzle-testing?date=2026-06-01")
            self.assertEqual(response_get.status_code, 200)
            data_get = response_get.json()
            self.assertEqual(data_get["status"], "success")
            self.assertEqual(len(data_get["logs"]), 1)
            self.assertEqual(data_get["logs"][0]["nozzle_id"], "HSD-2")
            self.assertEqual(data_get["logs"][0]["testing_volume_liters"], 5.0)
            
        finally:
            main.DB_PATH = original_main_db

if __name__ == "__main__":
    unittest.main()
