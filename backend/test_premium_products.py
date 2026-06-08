import os
import sys
import sqlite3
import pandas as pd
import unittest
import json
from unittest.mock import patch, MagicMock

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import premium_products
import price_registry
import reconciliation
import exporter
import main
import init_db

TEST_DB = os.path.join(BACKEND_DIR, "test_premium.db")
TEST_EXCEL = os.path.join(BACKEND_DIR, "test_premium.xlsx")

class TestPremiumProducts(unittest.TestCase):
    def setUp(self):
        # Override paths to use test DB and Excel
        self.original_db = price_registry.DB_PATH
        self.original_excel = exporter.DEFAULT_EXCEL_PATH
        self.original_main_db = main.DB_PATH
        self.original_recon_db = reconciliation.DB_PATH
        self.original_init_db = init_db.DB_PATH
        
        price_registry.DB_PATH = TEST_DB
        exporter.DB_PATH = TEST_DB
        exporter.DEFAULT_EXCEL_PATH = TEST_EXCEL
        main.DB_PATH = TEST_DB
        reconciliation.DB_PATH = TEST_DB
        init_db.DB_PATH = TEST_DB
        
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        if os.path.exists(TEST_EXCEL):
            os.remove(TEST_EXCEL)
            
        # Initial standard database tables setup
        init_db.initialize_database()
        main.init_db()
        
    def tearDown(self):
        price_registry.DB_PATH = self.original_db
        exporter.DB_PATH = self.original_db
        exporter.DEFAULT_EXCEL_PATH = self.original_excel
        main.DB_PATH = self.original_main_db
        reconciliation.DB_PATH = self.original_recon_db
        init_db.DB_PATH = self.original_init_db
        
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        if os.path.exists(TEST_EXCEL):
            os.remove(TEST_EXCEL)

    def test_schema_migration(self):
        """Verifies database tables correctly update with premium product columns."""
        # Create standard tables manually on a separate clean DB file to test migration
        temp_db = os.path.join(BACKEND_DIR, "temp_migration.db")
        if os.path.exists(temp_db):
            os.remove(temp_db)
            
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        # Create legacy daily_summary
        cursor.execute("""
        CREATE TABLE daily_summary (
            date TEXT PRIMARY KEY,
            total_hsd_liters REAL DEFAULT 0.0,
            total_ms_liters REAL DEFAULT 0.0
        )
        """)
        # Create legacy fuel_rates
        cursor.execute("""
        CREATE TABLE fuel_rates (
            date TEXT PRIMARY KEY,
            hsd_rate REAL NOT NULL,
            ms_rate REAL NOT NULL
        )
        """)
        conn.commit()
        conn.close()
        
        try:
            # 1. Apply premium products migration
            premium_products.migrate_premium_product_columns(temp_db)
            
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            
            # Verify daily_summary columns
            cursor.execute("PRAGMA table_info(daily_summary)")
            summary_cols = [c[1] for c in cursor.fetchall()]
            self.assertIn("total_regular_hsd_liters", summary_cols)
            self.assertIn("total_premium_hsd_liters", summary_cols)
            self.assertIn("total_regular_ms_liters", summary_cols)
            self.assertIn("total_premium_ms_liters", summary_cols)
            
            # Verify fuel_rates columns
            cursor.execute("PRAGMA table_info(fuel_rates)")
            rates_cols = [c[1] for c in cursor.fetchall()]
            self.assertIn("premium_hsd_rate", rates_cols)
            self.assertIn("premium_ms_rate", rates_cols)
            
            conn.close()
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_nozzle_sku_mapping(self):
        """Verifies shorthand brand names map accurately to canonical SKUs."""
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("XP 95 Nozzle 2"), "PREMIUM_MS")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("Speed Nozzle"), "PREMIUM_MS")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("95 Octane line"), "PREMIUM_MS")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("Xtragreen Nozzle 1"), "PREMIUM_HSD")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("Premium HSD"), "PREMIUM_HSD")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("Standard Diesel Nozzle"), "REGULAR_HSD")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("MS-1 (Regular Petrol)"), "REGULAR_MS")
        self.assertEqual(premium_products.map_nozzle_brand_to_sku("Unknown"), "REGULAR_MS")

    def test_price_registry_and_pricing_resolution(self):
        """Verifies rate fetches, null fallback deltas, and CSV imports work."""
        premium_products.migrate_premium_product_columns(TEST_DB)
        
        # Test rate resolution with null database entries (fallback logic)
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM fuel_rates")
        # insert base rates only
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-06-01", 90.0, 100.0))
        conn.commit()
        conn.close()
        
        rates = price_registry.get_rates_for_date("2026-06-01")
        self.assertIsNotNone(rates)
        self.assertEqual(rates["hsd_rate"], 90.0)
        self.assertEqual(rates["ms_rate"], 100.0)
        self.assertIsNone(rates["premium_hsd_rate"])
        self.assertIsNone(rates["premium_ms_rate"])
        
        # Verify resolve_variant_rate computes the correct fallbacks: HSD + 3.0, MS + 5.0
        self.assertEqual(premium_products.resolve_variant_rate(rates, "REGULAR_HSD"), 90.0)
        self.assertEqual(premium_products.resolve_variant_rate(rates, "PREMIUM_HSD"), 93.0)
        self.assertEqual(premium_products.resolve_variant_rate(rates, "REGULAR_MS"), 100.0)
        self.assertEqual(premium_products.resolve_variant_rate(rates, "PREMIUM_MS"), 105.0)
        
        # Test rate resolution when premium rates are explicitly stored
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
            VALUES (?, ?, ?, ?, ?)
        """, ("2026-06-02", 90.0, 100.0, 95.5, 108.5))
        conn.commit()
        conn.close()
        
        rates_2 = price_registry.get_rates_for_date("2026-06-02")
        self.assertEqual(premium_products.resolve_variant_rate(rates_2, "PREMIUM_HSD"), 95.5)
        self.assertEqual(premium_products.resolve_variant_rate(rates_2, "PREMIUM_MS"), 108.5)
        
        # Test importing from CSV
        csv_path = os.path.join(BACKEND_DIR, "test_premium_rates.csv")
        df_csv = pd.DataFrame({
            "date": ["2026-06-03"],
            "hsd_rate": [91.0],
            "ms_rate": [101.0],
            "premium_hsd_rate": [94.5],
            "premium_ms_rate": [106.5]
        })
        df_csv.to_csv(csv_path, index=False)
        
        try:
            price_registry.import_rate_csv(csv_path)
            rates_3 = price_registry.get_rates_for_date("2026-06-03")
            self.assertEqual(rates_3["hsd_rate"], 91.0)
            self.assertEqual(rates_3["premium_hsd_rate"], 94.5)
            self.assertEqual(rates_3["premium_ms_rate"], 106.5)
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_secondary_math_audit_and_calibrations(self):
        """Verifies the secondary Python audit correctly updates rates and aggregates volumes."""
        premium_products.migrate_premium_product_columns(TEST_DB)
        
        # Seed pricing for date 2026-06-04
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
            VALUES (?, 90.0, 100.0, 94.0, 107.0)
        """, ("2026-06-04",))
        conn.commit()
        conn.close()
        
        # Simulate Claude response JSON
        raw_output_json = {
            "date": "2026-06-04",
            "nozzles": [
                {
                    "nozzle_name": "XP95 Nozzle",
                    "fuel_type": "PREMIUM_MS",
                    "opening": 1000.0,
                    "closing": 1100.0,
                    "testing_liters": 0.0,
                    "transcribed_flow": 100.0,
                    "rate": 0.0  # Needs calibration
                },
                {
                    "nozzle_name": "Standard MS Nozzle",
                    "fuel_type": "REGULAR_MS",
                    "opening": 2000.0,
                    "closing": 2150.0,
                    "testing_liters": 5.0,
                    "transcribed_flow": 150.0,
                    "rate": 0.0  # Needs calibration
                }
            ]
        }
        
        # Trigger the secondary audit logic from ai_engine by mocking Claude's API call
        with patch("ai_engine.anthropic.Anthropic") as mock_anthropic:
            # mock messages create
            mock_client = MagicMock()
            mock_message = MagicMock()
            mock_message.content = [MagicMock(text=json.dumps(raw_output_json))]
            mock_client.messages.create.return_value = mock_message
            mock_anthropic.return_value = mock_client
            
            from ai_engine import run_claude_accounting_guardrails
            # Temporarily point DB to test DB inside ai_engine as well
            with patch("ai_engine.os.getenv", return_value="fake_api_key"):
                # We need price_registry.DB_PATH override to affect ai_engine's database query too
                with patch("price_registry.DB_PATH", TEST_DB):
                    audited_payload = run_claude_accounting_guardrails("fake_transcript")
                    
                    # Verify rates were calibrated from the database
                    nozzles = audited_payload["nozzles"]
                    self.assertEqual(nozzles[0]["rate"], 107.0) # Premium MS rate
                    self.assertEqual(nozzles[1]["rate"], 100.0) # Regular MS rate
                    
                    # Verify amounts were calculated correctly
                    self.assertEqual(nozzles[0]["amount_calculated"], 100.0 * 107.0)
                    self.assertEqual(nozzles[1]["amount_calculated"], 145.0 * 100.0) # net 145 liters
                    
                    # Verify aggregated totals
                    self.assertEqual(audited_payload["total_calculated_liters_ms"], 245.0) # 100 + 145
                    self.assertEqual(audited_payload["total_cash_calculated"], (100.0 * 107.0) + (145.0 * 100.0))

    def test_reconciliation_calculations(self):
        """Verifies daily operational variance handles premium rates and splits."""
        premium_products.migrate_premium_product_columns(TEST_DB)
        
        # 1. Populate database tables
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        # insert rates
        cursor.execute("""
            INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
            VALUES (?, 90.0, 100.0, 93.0, 106.0)
        """, ("2026-06-05",))
        # insert daily summary with splits
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary 
            (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified,
             total_regular_hsd_liters, total_premium_hsd_liters, total_regular_ms_liters, total_premium_ms_liters)
            VALUES (?, 300.0, 500.0, 77000.0, 10000.0, 5.0, 1, 200.0, 100.0, 400.0, 100.0)
        """, ("2026-06-05",))
        # insert daily ledger
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales)
            VALUES ('2026-06-05', 800.0, 77000.0, 67000.0, 0.0, 0.0, 0.0, 10000.0)
        """)
        # insert nozzle testing log to balance variance calculations
        cursor.execute("""
            INSERT OR REPLACE INTO nozzle_testing_logs (date, nozzle_id, product_type, testing_volume_liters, rts_verified)
            VALUES ('2026-06-05', 'HSD-1', 'HSD', 5.0, 1)
        """)
        conn.commit()
        conn.close()
        
        # 2. Save reconciliation dips (balanced stock)
        # Expected regular HSD: opening (5000) + receipt (0) - sales (200) + testing (5) = 4805
        # Expected premium HSD: opening (3000) + receipt (0) - sales (100) = 2900
        # Combined expected HSD stock: 4805 + 2900 = 7705 (without RTS return logic, total HSD expected book: opening + receipt - total HSD sales = 5000 + 0 - 300 = 4700 + 5 testing RTS = 4705)
        # HSD expected book stock is calculated using global totals: opening + receipt - total_sales + testing = 5000 + 0 - 300 + 5 = 4705
        reconciliation.save_reconciliation(
            date_str="2026-06-05",
            hsd_opening=5000.0,
            hsd_receipt=0.0,
            hsd_closing=4705.0, # zero variance
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=7500.0, # zero variance
            actual_cash=67000.0,
            digital_settlements=0.0,
            udhaar_entries=10000.0,
            db_path=TEST_DB
        )
        
        # 3. Calculate daily variance
        calc = reconciliation.calculate_daily_variance("2026-06-05", db_path=TEST_DB)
        
        # Assertions
        self.assertEqual(calc["hsd_variance_liters"], 0.0)
        self.assertEqual(calc["ms_variance_liters"], 0.0)
        
        # Expected cash revenue:
        # HSD expected revenue: (200.0 regular - 5.0 testing) * 90.0 + (100.0 premium) * 93.0 = 195 * 90 + 100 * 93 = 17550 + 9300 = 26850
        # MS expected revenue: (400.0 regular - 0.0 testing) * 100.0 + (100.0 premium) * 106.0 = 400 * 100 + 100 * 106 = 40000 + 10600 = 50600
        # Total expected: 26850 + 50600 = 77450
        self.assertEqual(calc["calculated_sales_value"], 77450.0)
        
        # Reconciled total = cash (67000) + digital (0) + udhaar (10000) = 77000
        # Variance: 77000 - 77450 = -450 shortage
        self.assertEqual(calc["cash_short_or_over"], -450.0)
        self.assertEqual(calc["cash_status"], "shortage")

    def test_save_ledger_day_and_excel_export_splits(self):
        """Verifies api endpoint saves splits and Excel export writes distinct columns."""
        premium_products.migrate_premium_product_columns(TEST_DB)
        
        # 1. Simulate saving ledger day via main.save_ledger_day
        payload = main.SaveLedgerDayRequest(
            date="2026-06-06",
            total_calculated_liters_hsd=300.0,
            total_calculated_liters_ms=500.0,
            total_cash_calculated=77000.0,
            total_credit_sales=10000.0,
            total_testing_deductions=5.0,
            total_regular_hsd_liters=200.0,
            total_premium_hsd_liters=100.0,
            total_regular_ms_liters=400.0,
            total_premium_ms_liters=100.0,
            nozzles=[
                {"nozzle_name": "HSD-1", "fuel_type": "REGULAR_HSD", "net_sales_liters": 200.0, "rate": 90.0, "amount_calculated": 18000.0},
                {"nozzle_name": "XTRAGREEN-1", "fuel_type": "PREMIUM_HSD", "net_sales_liters": 100.0, "rate": 93.0, "amount_calculated": 9300.0},
                {"nozzle_name": "MS-1", "fuel_type": "REGULAR_MS", "net_sales_liters": 400.0, "rate": 100.0, "amount_calculated": 40000.0},
                {"nozzle_name": "XP95-1", "fuel_type": "PREMIUM_MS", "net_sales_liters": 100.0, "rate": 106.0, "amount_calculated": 10600.0}
            ]
        )
        
        # Trigger save
        main.save_ledger_day(payload)
        
        # Verify daily_summary extended columns were saved
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT total_regular_hsd_liters, total_premium_hsd_liters, 
                   total_regular_ms_liters, total_premium_ms_liters 
            FROM daily_summary WHERE date = '2026-06-06'
        """)
        row = cursor.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 200.0)
        self.assertEqual(row[1], 100.0)
        self.assertEqual(row[2], 400.0)
        self.assertEqual(row[3], 100.0)
        
        # 2. Trigger spreadsheet generation to verify columns are split inside Excel sheet
        # Seed daily_ledger record so exporter flows can find it
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        raw_payload = {
            "date": "2026-06-06",
            "nozzles": [
                {"nozzle_name": "HSD-1", "sales_liters_calculated": 200.0},
                {"nozzle_name": "XTRAGREEN-1", "sales_liters_calculated": 100.0},
                {"nozzle_name": "MS-1", "sales_liters_calculated": 400.0},
                {"nozzle_name": "XP95-1", "sales_liters_calculated": 100.0}
            ]
        }
        cursor.execute("UPDATE daily_ledger SET raw_data = ? WHERE date = '2026-06-06'", (json.dumps(raw_payload),))
        conn.commit()
        conn.close()
        
        excel_out, _ = exporter.generate_accounting_export("2026-06-06")
        
        # Read the sheet to verify split columns are present and populated
        df_sheet = pd.read_excel(excel_out, sheet_name="Shift Readings")
        
        self.assertIn("Regular HSD Sold (L)", df_sheet.columns)
        self.assertIn("Premium HSD Sold (L)", df_sheet.columns)
        self.assertIn("Regular MS Sold (L)", df_sheet.columns)
        self.assertIn("Premium MS Sold (L)", df_sheet.columns)
        
        self.assertEqual(df_sheet.iloc[0]["Regular HSD Sold (L)"], 200.0)
        self.assertEqual(df_sheet.iloc[0]["Premium HSD Sold (L)"], 100.0)
        self.assertEqual(df_sheet.iloc[0]["Regular MS Sold (L)"], 400.0)
        self.assertEqual(df_sheet.iloc[0]["Premium MS Sold (L)"], 100.0)

if __name__ == "__main__":
    unittest.main()
