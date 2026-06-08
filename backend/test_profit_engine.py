#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Daily Gross Profit Calculation Engine.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
import shutil
import json
import pandas as pd

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import init_db
import migrations
import profit_engine
import purchase_registry
import price_registry
import reconciliation
import exporter
from premium_products import migrate_premium_product_columns

class TestProfitEngine(unittest.TestCase):
    
    def setUp(self):
        # Override paths to prevent polluting production directories
        self.original_db = exporter.DB_PATH
        self.original_excel = exporter.DEFAULT_EXCEL_PATH
        self.original_init_db = init_db.DB_PATH
        self.original_price_db = price_registry.DB_PATH
        
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        
        exporter.DB_PATH = self.temp_db_path
        price_registry.DB_PATH = self.temp_db_path
        init_db.DB_PATH = self.temp_db_path
        
        # Initialize isolated test database tables fully via init_db
        init_db.initialize_database()
        
        # Initialize stock_recon table
        reconciliation.init_recon_db(self.temp_db_path)
        
        # Create daily_ledger table explicitly
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
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
        # Restore original paths
        exporter.DB_PATH = self.original_db
        exporter.DEFAULT_EXCEL_PATH = self.original_excel
        init_db.DB_PATH = self.original_init_db
        price_registry.DB_PATH = self.original_price_db
        
        # Close file descriptor and remove temporary file cleanly
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_migration_version_8_schema(self):
        """Verify that the daily_summary table was successfully updated to include VERSION 8 columns."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        version = cursor.fetchone()[0]
        self.assertGreaterEqual(version, 8)
        
        cursor.execute("PRAGMA table_info(daily_summary)")
        columns = {c[1] for c in cursor.fetchall()}
        
        self.assertIn("gross_spread_usd", columns)
        self.assertIn("realized_profit", columns)
        conn.close()

    def test_calculate_daily_fuel_profit_basic(self):
        """Test profit calculations with explicit prices and inventory shortages."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        date_str = "2026-06-01"
        
        # 1. Populate daily_summary
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (
                date, total_hsd_liters, total_ms_liters, 
                total_regular_hsd_liters, total_premium_hsd_liters,
                total_regular_ms_liters, total_premium_ms_liters,
                total_cash_calculated, total_credit_sales, is_verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (date_str, 1000.0, 1500.0, 800.0, 200.0, 1200.0, 300.0, 250000.0, 50000.0))
        
        # 2. Populate fuel_rates (RSPs)
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
            VALUES (?, 90.0, 100.0, 95.0, 105.0)
        """, (date_str,))
        
        # 3. Populate purchase_cost_log (CPs)
        cursor.execute("""
            INSERT OR REPLACE INTO purchase_cost_log (effective_date, product_type, purchase_rate_per_liter, invoice_reference)
            VALUES (?, 'HSD', 85.0, 'INV-HSD-1')
        """, (date_str,))
        cursor.execute("""
            INSERT OR REPLACE INTO purchase_cost_log (effective_date, product_type, purchase_rate_per_liter, invoice_reference)
            VALUES (?, 'MS', 95.0, 'INV-MS-1')
        """, (date_str,))
        
        # 4. Populate stock_recon (so wet stock reconciliation finds variances)
        # Expected HSD: 5000 - 1000 = 4000. Closing is 3990 (-10 L shortage)
        # Expected MS: 6000 - 1500 = 4500. Closing is 4495 (-5 L shortage)
        cursor.execute("""
            INSERT OR REPLACE INTO stock_recon (
                date, hsd_opening_dip_liters, hsd_closing_dip_liters,
                ms_opening_dip_liters, ms_closing_dip_liters
            ) VALUES (?, 5000.0, 3990.0, 6000.0, 4495.0)
        """, (date_str,))
        
        conn.commit()
        
        # Calculate daily fuel profit
        result = profit_engine.calculate_daily_fuel_profit(
            date_string=date_str,
            db_path=self.temp_db_path,
            conn=conn
        )
        
        # Assert math logic
        # HSD Spread: (800 * (90 - 85)) + (200 * (95 - 85)) = 4000 + 2000 = 6000 INR
        # MS Spread: (1200 * (100 - 95)) + (300 * (105 - 95)) = 6000 + 3000 = 9000 INR
        # Total Spread: 6000 + 9000 = 15000 INR
        # Spread USD: 15000 / 83 = 180.72 USD
        # Variance adjustment: (10 * 85) + (5 * 95) = 850 + 475 = 1325 INR
        # Realized profit: 15000 - 1325 = 13675 INR
        
        self.assertEqual(result["date"], date_str)
        self.assertAlmostEqual(result["gross_spread_inr"], 15000.0)
        self.assertAlmostEqual(result["gross_spread_usd"], 180.72)
        self.assertAlmostEqual(result["variance_cost_adjustment"], 1325.0)
        self.assertAlmostEqual(result["realized_profit"], 13675.0)
        
        conn.close()

    def test_calculate_daily_fuel_profit_fallback_cp(self):
        """Verify that CP defaults are calculated correctly when pricing cost logs are missing."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        date_str = "2026-06-02"
        
        # Populate daily_summary
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (
                date, total_hsd_liters, total_ms_liters, 
                total_cash_calculated, total_credit_sales, is_verified
            ) VALUES (?, ?, ?, ?, ?, 0)
        """, (date_str, 1000.0, 1500.0, 240000.0, 40000.0))
        
        # Populate fuel_rates (RSPs)
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
            VALUES (?, 90.0, 100.0, NULL, NULL)
        """, (date_str,))
        
        # Do NOT populate purchase_cost_log. Should trigger:
        # HSD CP fallback: RSP - 3.00 = 87.0
        # MS CP fallback: RSP - 4.00 = 96.0
        
        # Populate stock_recon with positive variance (surpluses do not create shortage cost adjustments)
        cursor.execute("""
            INSERT OR REPLACE INTO stock_recon (
                date, hsd_opening_dip_liters, hsd_closing_dip_liters,
                ms_opening_dip_liters, ms_closing_dip_liters
            ) VALUES (?, 5000.0, 4010.0, 6000.0, 4510.0)
        """, (date_str,))
        
        conn.commit()
        
        # Run calculation
        result = profit_engine.calculate_daily_fuel_profit(
            date_string=date_str,
            db_path=self.temp_db_path,
            conn=conn
        )
        
        # HSD Spread: 1000 * (90 - 87) = 3000 INR
        # MS Spread: 1500 * (100 - 96) = 6000 INR
        # Total Spread: 9000 INR
        # Spread USD: 9000 / 83 = 108.43 USD
        # Shortage should be 0 because variances are positive (+10 L and +10 L)
        # Realized profit: 9000 INR
        
        self.assertAlmostEqual(result["gross_spread_inr"], 9000.0)
        self.assertAlmostEqual(result["gross_spread_usd"], 108.43)
        self.assertAlmostEqual(result["variance_cost_adjustment"], 0.0)
        self.assertAlmostEqual(result["realized_profit"], 9000.0)
        
        conn.close()

    def test_calculate_and_store_daily_profit_hook(self):
        """Verify that calculate_and_store_daily_profit correctly updates SQLite rows."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        date_str = "2026-06-03"
        
        # Seed daily_summary
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (
                date, total_hsd_liters, total_ms_liters, 
                gross_spread_usd, realized_profit, is_verified
            ) VALUES (?, ?, ?, 0.0, 0.0, 0)
        """, (date_str, 500.0, 500.0))
        
        # Seed fuel_rates (RSPs)
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate)
            VALUES (?, 90.0, 100.0)
        """, (date_str,))
        
        # Leave CP registry empty -> fallbacks apply: HSD CP = 87.0, MS CP = 96.0
        # HSD spread: 500 * 3 = 1500 INR
        # MS spread: 500 * 4 = 2000 INR
        # Total spread: 3500 INR -> USD = 42.17
        
        conn.commit()
        
        # Run storage hook
        profit_engine.calculate_and_store_daily_profit(
            date_string=date_str,
            db_path=self.temp_db_path,
            conn=conn
        )
        
        # Verify db was updated
        cursor.execute("SELECT gross_spread_usd, realized_profit FROM daily_summary WHERE date = ?", (date_str,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 42.17)
        self.assertAlmostEqual(row[1], 3500.0)
        
        conn.close()

    def test_excel_compilation_totals(self):
        """Verify that exporter functions include the profit columns and the Profit Accounting Totals rows."""
        # Setup temporary directories
        temp_dir = tempfile.mkdtemp()
        excel_path = os.path.join(temp_dir, "Pump_Accounts_Test.xlsx")
        
        try:
            # Re-route exporter DB_PATH and default excel paths
            exporter.DB_PATH = self.temp_db_path
            exporter.DEFAULT_EXCEL_PATH = excel_path
            
            # Setup a test database state
            conn = sqlite3.connect(self.temp_db_path)
            cursor = conn.cursor()
            
            date_str_1 = "2026-06-01"
            date_str_2 = "2026-06-02"
            
            # Daily Summary records
            cursor.execute("""
                INSERT OR REPLACE INTO daily_summary (
                    date, total_hsd_liters, total_ms_liters, 
                    total_cash_calculated, total_credit_sales, 
                    gross_spread_usd, realized_profit, is_verified
                ) VALUES (?, 100.0, 200.0, 30000.0, 5000.0, 10.0, 800.0, 1)
            """, (date_str_1,))
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_summary (
                    date, total_hsd_liters, total_ms_liters, 
                    total_cash_calculated, total_credit_sales, 
                    gross_spread_usd, realized_profit, is_verified
                ) VALUES (?, 150.0, 250.0, 40000.0, 7000.0, 15.0, 1200.0, 1)
            """, (date_str_2,))
            
            # Insert matching daily_ledger record with mock raw_data containing nozzle flows
            raw_payload_1 = {"date": date_str_1, "nozzles": []}
            raw_payload_2 = {"date": date_str_2, "nozzles": []}
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_ledger (date, total_sales_liters, total_amount_inr, raw_data)
                VALUES (?, 300.0, 30000.0, ?)
            """, (date_str_1, json.dumps(raw_payload_1)))
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_ledger (date, total_sales_liters, total_amount_inr, raw_data)
                VALUES (?, 400.0, 40000.0, ?)
            """, (date_str_2, json.dumps(raw_payload_2)))
            
            conn.commit()
            conn.close()
            
            # Run Excel compilation
            exporter.export_db_to_excel(excel_path)
            
            # Read back with Pandas to check sheet contents
            df_summary = pd.read_excel(excel_path, sheet_name="Daily Sales Summaries")
            
            # Verify columns renamed correctly
            self.assertIn("Gross Spread (USD)", df_summary.columns)
            self.assertIn("Realized Profit", df_summary.columns)
            
            # Verify totals row was appended to the bottom
            last_row = df_summary.iloc[-1]
            self.assertEqual(last_row["Date"], "Profit Accounting Totals")
            
            # Check totals sums:
            # HSD: 100 + 150 = 250
            # MS: 200 + 250 = 450
            # Cash: 30000 + 40000 = 70000
            # Credit: 5000 + 7000 = 12000
            # Gross Spread (USD): 10 + 15 = 25
            # Realized Profit: 800 + 1200 = 2000
            self.assertAlmostEqual(last_row["HSD Sold (Liters)"], 250.0)
            self.assertAlmostEqual(last_row["MS Sold (Liters)"], 450.0)
            self.assertAlmostEqual(last_row["Cash Calculated (INR)"], 70000.0)
            self.assertAlmostEqual(last_row["Credit Sales (INR)"], 12000.0)
            self.assertAlmostEqual(last_row["Gross Spread (USD)"], 25.0)
            self.assertAlmostEqual(last_row["Realized Profit"], 2000.0)
            
            # Now test shift export in generate_accounting_export
            # This generates export inside a pump_exports folder inside workspace dir
            # In our case, generate_accounting_export creates f"accounting_export_{date_string}.xlsx"
            excel_export_path, _ = exporter.generate_accounting_export(date_string="all")
            
            # Load shift readings sheet
            df_shift = pd.read_excel(excel_export_path, sheet_name="Shift Readings")
            
            # Verify columns exist
            self.assertIn("Gross Spread (USD)", df_shift.columns)
            self.assertIn("Realized Profit", df_shift.columns)
            
            # Verify totals row
            last_row_shift = df_shift.iloc[-1]
            self.assertEqual(last_row_shift["Date"], "Profit Accounting Totals")
            self.assertAlmostEqual(last_row_shift["Gross Spread (USD)"], 25.0)
            self.assertAlmostEqual(last_row_shift["Realized Profit"], 2000.0)
            
        finally:
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    unittest.main()
