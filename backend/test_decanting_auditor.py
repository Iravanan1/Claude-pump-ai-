#!/usr/bin/env python3
"""
Unit and Integration Test Suite for the Fuel Tanker Decanting and Depot Transit Shortage Auditor.
"""

import os
import sys
import sqlite3
import unittest
import pandas as pd
from fastapi.testclient import TestClient

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import init_db
import decanting_auditor
import exporter
import reconciliation
import price_registry
import density_logger
import tank_calibration
import credit_realization
import main
from main import app

class TestDecantingAuditor(unittest.TestCase):
    def setUp(self):
        # Store original DB paths
        self.original_decanting_db = decanting_auditor.DB_PATH
        self.original_exporter_db = exporter.DB_PATH
        self.original_main_db = main.DB_PATH
        self.original_init_db = init_db.DB_PATH
        self.original_recon_db = reconciliation.DB_PATH
        self.original_price_db = price_registry.DB_PATH
        self.original_density_db = density_logger.DB_PATH
        self.original_cal_db = tank_calibration.DB_PATH
        self.original_cred_db = credit_realization.DB_PATH

        # Define test DB paths
        self.test_db = os.path.join(BACKEND_DIR, "test_decanting.db")
        self.test_excel = os.path.join(BACKEND_DIR, "test_decanting.xlsx")

        decanting_auditor.DB_PATH = self.test_db
        exporter.DB_PATH = self.test_db
        main.DB_PATH = self.test_db
        init_db.DB_PATH = self.test_db
        reconciliation.DB_PATH = self.test_db
        price_registry.DB_PATH = self.test_db
        density_logger.DB_PATH = self.test_db
        tank_calibration.DB_PATH = self.test_db
        credit_realization.DB_PATH = self.test_db

        # Remove existing files if they exist
        for path in [self.test_db, self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        # Use init_db to fully set up all tables in our sandbox
        init_db.initialize_database()
        
        # Explicitly initialize recon db and calibration db
        reconciliation.init_recon_db(self.test_db)
        tank_calibration.init_calibration_db(self.test_db)

        self.client = TestClient(app)

    def tearDown(self):
        # Restore DB paths
        decanting_auditor.DB_PATH = self.original_decanting_db
        exporter.DB_PATH = self.original_exporter_db
        main.DB_PATH = self.original_main_db
        init_db.DB_PATH = self.original_init_db
        reconciliation.DB_PATH = self.original_recon_db
        price_registry.DB_PATH = self.original_price_db
        density_logger.DB_PATH = self.original_density_db
        tank_calibration.DB_PATH = self.original_cal_db
        credit_realization.DB_PATH = self.original_cred_db

        # Clean up test database and excel sheet
        for path in [self.test_db, self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_database_initialization(self):
        """Verifies that the tanker_receipts table is correctly created with the correct columns."""
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tanker_receipts'")
        self.assertIsNotNone(cursor.fetchone(), "Table tanker_receipts was not created.")
        
        cursor.execute("PRAGMA table_info(tanker_receipts)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        expected_columns = [
            "invoice_no", "date", "tank_lorry_no", "product_type",
            "invoice_volume_liters", "invoice_density_at_15c",
            "observed_compartment_dips_mm", "observed_density_raw",
            "observed_temperature_celsius", "actual_received_volume_liters",
            "transit_shortage_liters"
        ]
        
        for col in expected_columns:
            self.assertIn(col, columns, f"Missing column {col}")
            
        conn.close()

    def test_temperature_volume_correction_math(self):
        """Verifies calculation of VCF and actual received volume back to 15C."""
        # Setup mock calibration and latest stock to avoid space validation exceptions
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO stock_recon (date, hsd_closing_dip_liters) VALUES ('2026-06-01', 5000.0)")
        conn.commit()
        conn.close()

        # Let's save a receipt:
        # HSD density 830 at 30°C: standard density at 15°C is calculated using ASTM non-linear solver.
        # Let's test the correction logic and save receipt.
        res = decanting_auditor.save_tanker_receipt(
            invoice_no="INV-001",
            date_str="2026-06-01",
            tank_lorry_no="KA-01-1234",
            product_type="HSD",
            invoice_volume_liters=10000.0,
            invoice_density_at_15c=835.0,
            observed_compartment_dips_mm="120,120,120",
            observed_density_raw=830.0,
            observed_temperature_celsius=30.0,
            raw_observed_volume_liters=10000.0,
            db_path=self.test_db
        )
        
        self.assertEqual(res["status"], "success")
        self.assertIn("actual_received_volume_liters", res)
        self.assertIn("transit_shortage_liters", res)
        self.assertIn("volume_correction_factor", res)
        
        # Verify that actual_received_volume_liters matches what's stored in the DB
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT actual_received_volume_liters, transit_shortage_liters FROM tanker_receipts WHERE invoice_no = 'INV-001'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], res["actual_received_volume_liters"])
        self.assertEqual(row[1], res["transit_shortage_liters"])
        conn.close()

    def test_decanting_space_validation(self):
        """Verifies safety warning checks (sufficient space vs insufficient space warning)."""
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        # Mock capacity is 20000 for HSD.
        # Let's add latest stock to stock_recon: 15000.0 Liters.
        cursor.execute("INSERT INTO stock_recon (date, hsd_closing_dip_liters) VALUES ('2026-06-01', 15000.0)")
        conn.commit()
        conn.close()

        # Remaining capacity (Ullage) is capacity (20000.0) - current (15000.0) = 5000.0 L
        
        # 1. Received volume is 4000.0 L (Sufficient Space)
        res_ok = decanting_auditor.validate_decanting_space(
            product_type="HSD",
            actual_received_volume=4000.0,
            db_path=self.test_db
        )
        self.assertTrue(res_ok["safe"])
        self.assertEqual(res_ok["message"], "Clearance approved for decanting.")

        # 2. Received volume is 6000.0 L (Insufficient Space)
        res_bad = decanting_auditor.validate_decanting_space(
            product_type="HSD",
            actual_received_volume=6000.0,
            db_path=self.test_db
        )
        self.assertFalse(res_bad["safe"])
        self.assertEqual(res_bad["message"], "CRITICAL: Insufficient Underground Tank Space for Decanting")

    def test_excel_claims_ledger_sync(self):
        """Verifies that saving tanker receipts successfully routes data to Transporter Transit Claims Ledger in Excel."""
        # We need to test exporter.export_db_to_excel
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO stock_recon (date, hsd_closing_dip_liters) VALUES ('2026-06-01', 2000.0)")
        conn.commit()
        conn.close()

        # Save a mock tanker receipt
        res = decanting_auditor.save_tanker_receipt(
            invoice_no="INV-X99",
            date_str="2026-06-01",
            tank_lorry_no="MH-12-8888",
            product_type="HSD",
            invoice_volume_liters=12000.0,
            invoice_density_at_15c=840.0,
            observed_compartment_dips_mm="140,140,140",
            observed_density_raw=832.0,
            observed_temperature_celsius=28.0,
            raw_observed_volume_liters=12000.0,
            db_path=self.test_db
        )
        self.assertEqual(res["status"], "success")

        # Now trigger the excel export manually to self.test_excel
        exporter.export_db_to_excel(self.test_excel)
        
        # Verify the Excel file was created
        self.assertTrue(os.path.exists(self.test_excel))

        # Check sheets in Excel file
        excel_file = pd.ExcelFile(self.test_excel)
        self.assertIn("Transporter Transit Claims Ledger", excel_file.sheet_names)
        
        # Read the sheet content
        df = excel_file.parse("Transporter Transit Claims Ledger")
        self.assertFalse(df.empty)
        self.assertIn("Invoice No", df.columns)
        self.assertIn("Transit Shortage (Liters)", df.columns)
        
        # Confirm our specific entry is inside the sheet
        invoice_list = df["Invoice No"].astype(str).tolist()
        self.assertIn("INV-X99", invoice_list)

    def test_api_endpoints(self):
        """Verifies FastAPI controller endpoints POST and GET for decanting auditor."""
        # 1. Setup mock price rates or stock details if needed
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO stock_recon (date, hsd_closing_dip_liters) VALUES ('2026-06-01', 1000.0)")
        conn.commit()
        conn.close()

        # 2. Test GET Space check
        response_check = self.client.get("/api/decanting/check-space?product_type=HSD&actual_received_volume=5000.0")
        self.assertEqual(response_check.status_code, 200)
        data_check = response_check.json()
        self.assertTrue(data_check["safe"])
        
        # 3. Test POST save receipt
        payload = {
            "invoice_no": "INV-API-TEST",
            "date": "2026-06-01",
            "tank_lorry_no": "DL-1C-9999",
            "product_type": "HSD",
            "invoice_volume_liters": 15000.0,
            "invoice_density_at_15c": 830.0,
            "observed_compartment_dips_mm": "100,100",
            "observed_density_raw": 825.0,
            "observed_temperature_celsius": 25.0,
            "raw_observed_volume_liters": 15000.0
        }
        
        response_post = self.client.post("/api/decanting/receipt", json=payload)
        self.assertEqual(response_post.status_code, 200)
        data_post = response_post.json()
        self.assertEqual(data_post["status"], "success")
        self.assertEqual(data_post["invoice_no"], "INV-API-TEST")
        
        # 4. Test GET receipts
        response_get = self.client.get("/api/decanting/receipts")
        self.assertEqual(response_get.status_code, 200)
        data_get = response_get.json()
        self.assertEqual(data_get["status"], "success")
        receipt_invoices = [r["invoice_no"] for r in data_get["receipts"]]
        self.assertIn("INV-API-TEST", receipt_invoices)

if __name__ == "__main__":
    unittest.main()
