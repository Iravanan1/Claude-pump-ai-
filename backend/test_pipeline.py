#!/usr/bin/env python3
"""
System Integration Test Pipeline for PumpAI.
Runs a controlled trial execution of the complete software pipeline:
1. Sandbox Setup (Isolated DB and exports folder).
2. Phase 1: OpenCV Shadow Removal Preprocessing.
3. Phase 2: LLM Vision & Accounting Orchestrations (Mocked).
4. Phase 3: DB Commits via FastAPI /api/save-ledger-day Endpoint.
5. Phase 4: openpyxl Excel & PetroByte CSV Compilation.
6. Automated Integrity Asserts.
7. Print Factual Console Report & Clean Up.
"""

import os
import sys
import shutil
import sqlite3
import unittest
import numpy as np
import cv2
import json
import pandas as pd
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# 1. Sandbox Path Resolution
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

TEST_DB_PATH = os.path.join(BACKEND_DIR, "test_pump_accounts.db")
TEST_EXPORTS_DIR = os.path.join(BACKEND_DIR, "test_exports")
RAW_IMAGE_1 = os.path.join(BACKEND_DIR, "test_raw_day1.png")
RAW_IMAGE_2 = os.path.join(BACKEND_DIR, "test_raw_day2.png")

# 2. Dynamic Module Path Redirects
import init_db
import main
import exporter
import price_registry
import density_logger
import tank_calibration
import credit_realization
import card_settlements
import reconciliation
import cost_tracker
import state_tracker
import bulk_importer
import bank_matcher
import credit_guard
import dsm_tracker
import evaporation_handler
import local_analytics
import processor
import ai_engine

# Assign temporary test DB path across all active modules
init_db.DB_PATH = TEST_DB_PATH
main.DB_PATH = TEST_DB_PATH
exporter.DB_PATH = TEST_DB_PATH
price_registry.DB_PATH = TEST_DB_PATH
density_logger.DB_PATH = TEST_DB_PATH
tank_calibration.DB_PATH = TEST_DB_PATH
credit_realization.DB_PATH = TEST_DB_PATH
card_settlements.DB_PATH = TEST_DB_PATH
reconciliation.DB_PATH = TEST_DB_PATH
cost_tracker.DB_PATH = TEST_DB_PATH
state_tracker.DB_PATH = TEST_DB_PATH
bulk_importer.DB_PATH = TEST_DB_PATH
bank_matcher.DB_PATH = TEST_DB_PATH
credit_guard.DB_PATH = TEST_DB_PATH
dsm_tracker.DB_PATH = TEST_DB_PATH
evaporation_handler.DB_PATH = TEST_DB_PATH
local_analytics.DB_PATH = TEST_DB_PATH

# Redirect export spreadsheets
exporter.DEFAULT_EXCEL_PATH = os.path.join(TEST_EXPORTS_DIR, "test_ledger.xlsx")
main.EXCEL_PATH = os.path.join(TEST_EXPORTS_DIR, "test_ledger.xlsx")
local_analytics.CHARTS_DIR = os.path.join(TEST_EXPORTS_DIR, "charts")

# Mock/Redirect exports folder inside exporter.py local functions
original_join = os.path.join
def custom_join(*args):
    joined = original_join(*args)
    if isinstance(joined, str):
        if "pump_exports" in joined:
            joined = joined.replace("pump_exports", "backend/test_exports")
    elif isinstance(joined, bytes):
        if b"pump_exports" in joined:
            joined = joined.replace(b"pump_exports", b"backend/test_exports")
    return joined
exporter.os.path.join = custom_join


class TestEndToEndPipeline(unittest.TestCase):
    def setUp(self):
        """Sets up isolated sandbox directories, temporary DB schemas, and generates mock register images."""
        # Ensure clean sandbox directory states
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        if os.path.exists(TEST_EXPORTS_DIR):
            shutil.rmtree(TEST_EXPORTS_DIR)
            
        os.makedirs(TEST_EXPORTS_DIR, exist_ok=True)
        os.makedirs(local_analytics.CHARTS_DIR, exist_ok=True)
        
        # Initialize databases
        init_db.initialize_database()
        main.init_db()
        
        # Setup FastAPI client
        self.client = TestClient(main.app)
        
        # Seed fuel reference rates for our operations dates (June 1 and June 2, 2026)
        conn = sqlite3.connect(TEST_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate) VALUES ('2026-06-01', 90.0, 100.0)")
        cursor.execute("INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate) VALUES ('2026-06-02', 90.0, 100.0)")
        conn.commit()
        conn.close()
        
        # Generate two simple mock PNG register sheets (Phase 1 inputs)
        # Create 3-channel white canvas
        img1 = np.ones((800, 600, 3), dtype=np.uint8) * 255
        img2 = np.ones((800, 600, 3), dtype=np.uint8) * 255
        
        # Draw some mock gridlines and numbers to simulate clean register pages
        cv2.rectangle(img1, (50, 50), (550, 750), (200, 200, 200), 2)
        cv2.putText(img1, "DATE: 01-06-2026", (70, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        cv2.putText(img1, "HSD: Opening=12000, Closing=13000", (70, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        cv2.putText(img1, "MS: Opening=24000, Closing=25500", (70, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        
        cv2.rectangle(img2, (50, 50), (550, 750), (200, 200, 200), 2)
        cv2.putText(img2, "DATE: 02-06-2026", (70, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        
        cv2.imwrite(RAW_IMAGE_1, img1)
        cv2.imwrite(RAW_IMAGE_2, img2)
        
        self.raw_images = [RAW_IMAGE_1, RAW_IMAGE_2]
        self.optimized_images = []
        
        # Start validate_image_clarity patcher to ensure integration test mock uploads bypass quality checks
        self.clarity_patcher = patch("image_guard.validate_image_clarity", return_value={"success": True, "status": "OK", "focus_score": 999.0, "contrast_score": 999.0})
        self.mock_clarity = self.clarity_patcher.start()

    def tearDown(self):
        """Cleans up raw PNGs, test databases, and sandbox test_exports folder on test conclusion."""
        # Restore original os.path.join
        exporter.os.path.join = original_join

        # Stop validate_image_clarity patcher
        self.clarity_patcher.stop()
        
        # Close connection pools if any
        
        # Remove raw mock images
        for fpath in self.raw_images:
            if os.path.exists(fpath):
                os.remove(fpath)
                
        # Remove optimized images cached during execution
        for fpath in self.optimized_images:
            if os.path.exists(fpath):
                os.remove(fpath)
                
        # Prune test database and test exports folder
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass
                
        if os.path.exists(TEST_EXPORTS_DIR):
            shutil.rmtree(TEST_EXPORTS_DIR)
            
        # Clean up processed_images folder if it has any temporary file we wrote
        for f in os.listdir(processor.PROCESSED_DIR):
            if f.startswith("opt_"):
                fpath = os.path.join(processor.PROCESSED_DIR, f)
                try:
                    os.remove(fpath)
                except Exception:
                    pass

    @patch("ai_engine.run_gemini_vision_extraction")
    @patch("ai_engine.run_claude_accounting_guardrails")
    def test_complete_software_pipeline(self, mock_claude, mock_gemini):
        """Executes the full pipeline sequentially and asserts standard system compliance."""
        
        # Mock responses from LLM layer for 2 consecutive register days (Hinglish comments preserved)
        mock_gemini.side_effect = [
            "Raw text transcription for 2026-06-01. Contains गोपालराम details.",
            "Raw text transcription for 2026-06-02. Perfect accounting log."
        ]
        
        day1_payload = {
            "date": "2026-06-01",
            "total_calculated_liters_hsd": 1000.0,
            "total_calculated_liters_ms": 1500.0,
            "total_cash_calculated": 240000.0,
            "total_credit_sales": 5000.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": ["गोपालराम accounts balanced correctly", "UPI drop of ₹500 verified"],
            "nozzles": [
                {
                    "nozzle_name": "HSD-1",
                    "fuel_type": "HSD",
                    "opening": 12000.0,
                    "closing": 13000.0,
                    "sales_liters_calculated": 1000.0,
                    "rate": 90.0,
                    "amount_calculated": 90000.0,
                    "arithmetic_valid": True
                },
                {
                    "nozzle_name": "MS-1",
                    "fuel_type": "MS",
                    "opening": 24000.0,
                    "closing": 25500.0,
                    "sales_liters_calculated": 1500.0,
                    "rate": 100.0,
                    "amount_calculated": 150000.0,
                    "arithmetic_valid": True
                }
            ],
            "credit_sales": [
                {"party_name": "गोपालराम Transport", "vehicle_no": "RJ-14-GA-1234", "amount": 3000.0, "remarks": "HSD credit sale"},
                {"party_name": "Jagveer", "vehicle_no": "HR-26-AB-5678", "amount": 2000.0, "remarks": "MS credit sale"}
            ],
            "cash_expenses": [
                {"party_name": "Office Chai", "amount": 150.0, "remarks": "tea for staff"}
            ],
            "dsm_shifts": [],
            "lube_sales": [],
            "card_settlements": [],
            "credit_realizations": []
        }
        
        day2_payload = {
            "date": "2026-06-02",
            "total_calculated_liters_hsd": 800.0,
            "total_calculated_liters_ms": 1200.0,
            "total_cash_calculated": 192000.0,
            "total_credit_sales": 3000.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": ["Perfect accounting, no anomalies logged"],
            "nozzles": [
                {
                    "nozzle_name": "HSD-1",
                    "fuel_type": "HSD",
                    "opening": 13000.0,
                    "closing": 13800.0,
                    "sales_liters_calculated": 800.0,
                    "rate": 90.0,
                    "amount_calculated": 72000.0,
                    "arithmetic_valid": True
                },
                {
                    "nozzle_name": "MS-1",
                    "fuel_type": "MS",
                    "opening": 25500.0,
                    "closing": 26700.0,
                    "sales_liters_calculated": 1200.0,
                    "rate": 100.0,
                    "amount_calculated": 120000.0,
                    "arithmetic_valid": True
                }
            ],
            "credit_sales": [
                {"party_name": "गोपालराम Transport", "vehicle_no": "RJ-14-GA-1234", "amount": 3000.0, "remarks": "HSD credit sale"}
            ],
            "cash_expenses": [],
            "dsm_shifts": [],
            "lube_sales": [],
            "card_settlements": [],
            "credit_realizations": []
        }
        
        mock_claude.side_effect = [day1_payload, day2_payload]
        
        # ── Phase 1: Preprocessing ──
        print("\n[STEP 1] Executing OpenCV shadow division and advanced morphology...")
        for raw_img in self.raw_images:
            opt_path = processor.optimize_register_image(raw_img)
            self.assertTrue(os.path.exists(opt_path), f"Optimized image failed to save for {raw_img}!")
            self.optimized_images.append(opt_path)
        print("  => Preprocessing Successful. Optimized images cached on disk.")
        
        # ── Phase 2: LLM Vision Extraction ──
        print("[STEP 2] Simulating LLM domain glossary OCR and math verification layers...")
        extracted_payloads = []
        for opt_img in self.optimized_images:
            parsed_json = ai_engine.analyze_register_sheet(opt_img)
            self.assertIn("date", parsed_json)
            self.assertIn("nozzles", parsed_json)
            extracted_payloads.append(parsed_json)
        print("  => LLM Mock extraction executed cleanly.")
        
        # ── Phase 3: DB Commits via Endpoint ──
        print("[STEP 3] Performing client endpoint DB saves via POST /api/save-ledger-day...")
        for payload in extracted_payloads:
            # We hit the local test client API server
            response = self.client.post("/api/save-ledger-day", json=payload)
            self.assertEqual(response.status_code, 200, f"Failed to commit day: {response.text}")
        print("  => Database commits executed successfully.")
        
        # ── Phase 4: Excel Sync Compilation ──
        print("[STEP 4] Executing openpyxl continuous accounting excel compilation...")
        excel_out, csv_out = exporter.generate_accounting_export(date_string="all")
        self.assertTrue(os.path.exists(excel_out), "Accounting master Excel workbook was not created!")
        self.assertTrue(os.path.exists(csv_out), "PetroByte CSV double-entry sync log was not created!")
        print(f"  => Spreadsheet exported successfully to: {excel_out}")
        
        # ── Automated Integrity Asserts ──
        print("[STEP 5] Validating data integrity, arithmetic nozzle sums, and Hinglish encodings...")
        conn = sqlite3.connect(TEST_DB_PATH)
        cursor = conn.cursor()
        
        # A. SQLite Row Count checks
        cursor.execute("SELECT COUNT(*) FROM daily_summary")
        summary_count = cursor.fetchone()[0]
        self.assertEqual(summary_count, 2, "Exact mismatch in daily_summary records count!")
        
        cursor.execute("SELECT COUNT(*) FROM daily_ledger")
        ledger_count = cursor.fetchone()[0]
        self.assertEqual(ledger_count, 2, "Exact mismatch in daily_ledger records count!")
        
        cursor.execute("SELECT COUNT(*) FROM ledger_entries")
        entries_count = cursor.fetchone()[0]
        self.assertEqual(entries_count, 4, "Exact mismatch in ledger_entries detailed records count!")
        
        # B. Nozzle Sales Totals Excel validations
        # Load the compiled Excel back into pandas
        df_sheet = pd.read_excel(excel_out, sheet_name="Shift Readings")
        self.assertEqual(len(df_sheet), 3, "Compiled Shift Readings excel does not have exactly 2 days + totals row!")
        
        # Day 1 Excel Nozzle checking
        day1_row = df_sheet[df_sheet["Date"] == "2026-06-01"].iloc[0]
        # Liters sold = MS + HSD = 1000 + 1500 = 2500
        self.assertEqual(float(day1_row["Total Liters Sold"]), 2500.0)
        self.assertEqual(float(day1_row["Calculated Fuel Cash"]), 240000.0)
        self.assertIn("HSD-1: 1000.0 L", day1_row["Nozzle Flows"])
        self.assertIn("MS-1: 1500.0 L", day1_row["Nozzle Flows"])
        
        # Verify totals row at the bottom
        totals_row = df_sheet.iloc[2]
        self.assertEqual(totals_row["Date"], "Profit Accounting Totals")
        self.assertAlmostEqual(totals_row["Total Liters Sold"], 4500.0)  # 2500 (day1) + 2000 (day2)
        self.assertAlmostEqual(totals_row["Calculated Fuel Cash"], 432000.0)  # 240000 (day1) + 192000 (day2)
        
        # C. Non-truncation and Hindi/Hinglish Text Encoding verifications
        # Query raw text from SQLite to verify decrypt operations
        cursor.execute("SELECT party_name, type FROM ledger_entries WHERE date = '2026-06-01' AND type = 'udhaar'")
        db_rows = cursor.fetchall()
        
        from crypto_vault import decrypt_field
        decrypted_parties = [decrypt_field(r[0], return_type=str) for r in db_rows]
        self.assertIn("Gopalram Ji Dhaba", decrypted_parties, "Hinglish customer name was corrupted or truncated!")
        
        # Fetch daily ledger raw_data payload directly
        cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = '2026-06-01'")
        encrypted_raw = json.loads(cursor.fetchone()[0])
        from crypto_vault import decrypt_raw_data
        decrypted_raw = decrypt_raw_data(encrypted_raw)
        
        warnings_read = decrypted_raw.get("hinglish_notes", [])
        self.assertIn("गोपालराम accounts balanced correctly", warnings_read, "Hinglish warning log text got corrupted!")
        
        conn.close()
        print("  => All 3 Automated Integrity Asserts passed triumphantly!")


if __name__ == "__main__":
    print("======================================================================")
    print("                  RUNNING SYSTEM INTEGRATION TEST                     ")
    print("======================================================================")
    
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestEndToEndPipeline)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n\033[92m[PASS] OpenCV, [PASS] LLM Layer, [PASS] DB Commit, [PASS] Excel Sync. Integration Test Successful.\033[0m\n")
        sys.exit(0)
    else:
        print("\n\033[91m[FAIL] System Integration Test Failed. Some components failed validation checks.\033[0m\n")
        sys.exit(1)
