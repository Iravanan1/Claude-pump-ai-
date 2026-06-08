import os
import sys
import sqlite3
import shutil
import unittest
import pandas as pd
import json

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import exporter
import init_db

class TestExporter(unittest.TestCase):
    def setUp(self):
        # Override paths to prevent polluting production directories
        self.original_db = exporter.DB_PATH
        self.original_excel = exporter.DEFAULT_EXCEL_PATH
        self.original_init_db = init_db.DB_PATH
        
        self.test_db = os.path.join(BACKEND_DIR, "test_ledger.db")
        self.test_excel = os.path.join(BACKEND_DIR, "test_ledger.xlsx")
        
        exporter.DB_PATH = self.test_db
        exporter.DEFAULT_EXCEL_PATH = self.test_excel
        init_db.DB_PATH = self.test_db
        
        # Clean up files if they already exist
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        if os.path.exists(self.test_excel):
            os.remove(self.test_excel)
            
        # Re-initialize isolated test database tables
        init_db.initialize_database()
        
        # Resolve test exports directory
        self.test_workspace_dir = os.path.dirname(BACKEND_DIR)
        self.test_exports_dir = os.path.join(self.test_workspace_dir, "pump_exports")
        
        # Clean up any pre-existing test files in exports dir
        self.test_date = "2026-06-10"
        self.expected_excel_path = os.path.join(self.test_exports_dir, f"accounting_export_{self.test_date}.xlsx")
        self.expected_csv_path = os.path.join(self.test_exports_dir, f"petrobyte_sync_{self.test_date}.csv")
        
        if os.path.exists(self.expected_excel_path):
            os.remove(self.expected_excel_path)
        if os.path.exists(self.expected_csv_path):
            os.remove(self.expected_csv_path)

    def tearDown(self):
        # Restore original paths
        exporter.DB_PATH = self.original_db
        exporter.DEFAULT_EXCEL_PATH = self.original_excel
        init_db.DB_PATH = self.original_init_db
        
        # Clean up test files
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        if os.path.exists(self.test_excel):
            os.remove(self.test_excel)
        if os.path.exists(self.expected_excel_path):
            os.remove(self.expected_excel_path)
        if os.path.exists(self.expected_csv_path):
            os.remove(self.expected_csv_path)

    def test_generate_accounting_export_pipelines(self):
        """Verifies Excel sheets compilation, styled margins, and PetroByte transaction CSV layout."""
        # 1. Populate mock database entries for date 2026-06-10
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Insert test daily summary
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified)
            VALUES (?, 120.50, 240.20, 38500.00, 12000.00, 5.0, 1)
        """, (self.test_date,))
        
        # Insert test ledger credit sales (udhaar) and expenses (expense)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, 'Bhim Singh Transport', 'HR-55-AA-9999', 12000.00, 'udhaar', 'HSD credit sale')
        """, (self.test_date,))
        
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, 'Office Tea and Snacks', 'N/A', 350.00, 'expense', 'Tea')
        """, (self.test_date,))
        
        # Create matching daily_ledger record with mock raw_data containing nozzle flows
        raw_payload = {
            "date": self.test_date,
            "nozzles": [
                {"nozzle_name": "MS-1", "sales_liters_calculated": 140.20},
                {"nozzle_name": "MS-2", "sales_liters_calculated": 100.00},
                {"nozzle_name": "HSD-1", "sales_liters_calculated": 120.50}
            ]
        }
        raw_json_str = json.dumps(raw_payload)
        
        # Ensure daily_ledger table exists in isolated test DB
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
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ledger (date, total_sales_liters, total_amount_inr, raw_data)
            VALUES (?, 360.70, 38500.00, ?)
        """, (self.test_date, raw_json_str))
        
        conn.commit()
        conn.close()
        
        # 2. Trigger accounting export pipelines for specific test date
        excel_out, csv_out = exporter.generate_accounting_export(self.test_date)
        
        # 3. Assertions for generated file paths
        self.assertEqual(excel_out, self.expected_excel_path)
        self.assertEqual(csv_out, self.expected_csv_path)
        self.assertTrue(os.path.exists(self.expected_excel_path), "Excel output file was not created")
        self.assertTrue(os.path.exists(self.expected_csv_path), "CSV output file was not created")
        
        # 4. Assertions for Excel sheets content
        xls = pd.ExcelFile(self.expected_excel_path)
        self.assertIn("Shift Readings", xls.sheet_names)
        self.assertIn("Ledger Entries", xls.sheet_names)
        
        df_sheet_shift = pd.read_excel(self.expected_excel_path, sheet_name="Shift Readings")
        self.assertEqual(len(df_sheet_shift), 2)  # 1 day + 1 totals row
        self.assertEqual(df_sheet_shift.iloc[0]["Date"], self.test_date)
        # Check formatted flows match raw nozzles JSON
        self.assertIn("MS-1: 140.2 L", df_sheet_shift.iloc[0]["Nozzle Flows"])
        self.assertIn("HSD-1: 120.5 L", df_sheet_shift.iloc[0]["Nozzle Flows"])
        self.assertEqual(df_sheet_shift.iloc[0]["Total Liters Sold"], 360.70)
        self.assertEqual(df_sheet_shift.iloc[0]["Calculated Fuel Cash"], 38500.00)
        
        # Check totals row contents
        totals_row = df_sheet_shift.iloc[1]
        self.assertEqual(totals_row["Date"], "Profit Accounting Totals")
        self.assertAlmostEqual(totals_row["Total Liters Sold"], 360.70)
        self.assertAlmostEqual(totals_row["Calculated Fuel Cash"], 38500.00)
        
        df_sheet_ledger = pd.read_excel(self.expected_excel_path, sheet_name="Ledger Entries")
        df_sheet_ledger = df_sheet_ledger.sort_values(by="Account Head").reset_index(drop=True)
        self.assertEqual(len(df_sheet_ledger), 2)
        self.assertEqual(df_sheet_ledger.iloc[0]["Account Head"], "Bhim Singh Transport")
        self.assertEqual(df_sheet_ledger.iloc[0]["Transaction Type"], "udhaar")
        self.assertEqual(df_sheet_ledger.iloc[1]["Account Head"], "Office Tea and Snacks")
        self.assertEqual(df_sheet_ledger.iloc[1]["Transaction Type"], "expense")
        
        # 5. Assertions for PetroByte CSV layout content
        df_csv = pd.read_csv(self.expected_csv_path)
        # Verify headers match PetroByte import schema exactly
        expected_headers = ["Date", "Ledger Name", "Voucher Type", "Account Debit", "Account Credit", "Narration"]
        self.assertListEqual(list(df_csv.columns), expected_headers)
        
        # Expected double-entry balanced rows:
        # Receipt Leg 1 (Cash Sale Credit): Credit = 26500
        # Receipt Leg 2 (Cash Debit): Debit = 26500
        # Sale Leg 1 (Bhim Singh Transport Debit): Debit = 12000
        # Sale Leg 2 (Sales Credit): Credit = 12000
        # Payment Leg 1 (Office Tea and Snacks Debit): Debit = 350
        # Payment Leg 2 (Cash Credit): Credit = 350
        self.assertEqual(len(df_csv), 6)
        
        # Cash Sale Credit Row
        row_cash_credit = df_csv[(df_csv["Ledger Name"] == "Cash Sale") & (df_csv["Voucher Type"] == "Receipt")].iloc[0]
        self.assertEqual(row_cash_credit["Account Credit"], 26500.00)
        self.assertEqual(row_cash_credit["Account Debit"], 0.0)
        self.assertEqual(row_cash_credit["Narration"], "Daily net cash fuel sales")
        
        # Cash Sale Debit Row
        row_cash_debit = df_csv[(df_csv["Ledger Name"] == "Cash") & (df_csv["Voucher Type"] == "Receipt")].iloc[0]
        self.assertEqual(row_cash_debit["Account Debit"], 26500.00)
        self.assertEqual(row_cash_debit["Account Credit"], 0.0)
        
        # Credit Sale Row
        row_credit = df_csv[(df_csv["Ledger Name"] == "Bhim Singh Transport") & (df_csv["Voucher Type"] == "Sale")].iloc[0]
        self.assertEqual(row_credit["Account Debit"], 12000.00)
        self.assertEqual(row_credit["Account Credit"], 0.0)
        self.assertIn("Bhim Singh Transport", row_credit["Narration"])
        
        # Cash Expense Row
        row_expense = df_csv[(df_csv["Ledger Name"] == "Office Tea and Snacks") & (df_csv["Voucher Type"] == "Payment")].iloc[0]
        self.assertEqual(row_expense["Account Debit"], 350.00)
        self.assertEqual(row_expense["Account Credit"], 0.0)
        self.assertEqual(row_expense["Narration"], "Expense: Tea")

if __name__ == "__main__":
    unittest.main()
