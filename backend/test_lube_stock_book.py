#!/usr/bin/env python3
"""
Unit and Integration Test Suite for the Lubricant and Grease Stock Book Reconciliation Engine.
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
import lube_stock_book
import lube_sales
import exporter
import main
from main import app

class TestLubeStockBook(unittest.TestCase):
    def setUp(self):
        # Store original DB paths
        self.original_lube_stock_db = lube_stock_book.DB_PATH
        self.original_lube_sales_db = lube_sales.DB_PATH
        self.original_exporter_db = exporter.DB_PATH
        self.original_init_db = init_db.DB_PATH
        self.original_main_db = main.DB_PATH

        # Define test DB paths
        self.test_db = os.path.join(BACKEND_DIR, "test_lube_stock.db")
        self.test_excel = os.path.join(BACKEND_DIR, "test_lube_stock.xlsx")

        lube_stock_book.DB_PATH = self.test_db
        lube_sales.DB_PATH = self.test_db
        exporter.DB_PATH = self.test_db
        init_db.DB_PATH = self.test_db
        main.DB_PATH = self.test_db

        # Remove existing files if they exist
        for path in [self.test_db, self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        # Initialize the database and all standard tables
        init_db.initialize_database()
        lube_stock_book.init_lube_stock_db(self.test_db)
        lube_sales.init_lube_db(self.test_db)

        self.client = TestClient(app)

    def tearDown(self):
        # Restore DB paths
        lube_stock_book.DB_PATH = self.original_lube_stock_db
        lube_sales.DB_PATH = self.original_lube_sales_db
        exporter.DB_PATH = self.original_exporter_db
        init_db.DB_PATH = self.original_init_db
        main.DB_PATH = self.original_main_db

        # Clean up test database and excel sheet
        for path in [self.test_db, self.test_excel]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_database_initialization(self):
        """Verifies that lube_inventory_ledger table is correctly created with columns."""
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lube_inventory_ledger'")
        self.assertIsNotNone(cursor.fetchone(), "Table lube_inventory_ledger was not created.")
        
        cursor.execute("PRAGMA table_info(lube_inventory_ledger)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        expected_columns = [
            "item_sku", "item_name", "opening_stock_units", "inward_receipt_units",
            "outward_sold_units", "expected_closing_stock", "actual_physical_audit_stock",
            "inventory_shortage_variance"
        ]
        
        for col in expected_columns:
            self.assertIn(col, columns, f"Missing column: {col}")
            
        conn.close()

    def test_rollup_reconciliation_math(self):
        """Verifies the rollup calculations and expected closing stocks."""
        # 1. Add item to ledger
        sku = "Servo_4T_1L"
        name = "Servo 4T 1L"
        lube_stock_book.save_lube_inventory_item(
            item_sku=sku,
            item_name=name,
            opening_stock=100.0,
            inward_receipt=50.0,
            db_path=self.test_db
        )
        
        # 2. Add some sales to inventory_sales for June 2026
        lube_sales.save_lube_sale("2026-06-05", name, 10.0, 350.0, 3500.0, self.test_db)
        lube_sales.save_lube_sale("2026-06-12", name, 15.0, 350.0, 5250.0, self.test_db)
        # Sales in July should not be aggregated for June
        lube_sales.save_lube_sale("2026-07-01", name, 5.0, 350.0, 1750.0, self.test_db)
        
        # 3. Perform reconciliation for June 2026
        res = lube_stock_book.compute_running_lube_book(sku, "2026-06", self.test_db)
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["item_sku"], sku)
        self.assertEqual(res["opening_stock_units"], 100.0)
        self.assertEqual(res["inward_receipt_units"], 50.0)
        self.assertEqual(res["outward_sold_units"], 25.0) # 10.0 + 15.0
        self.assertEqual(res["expected_closing_stock"], 125.0) # 100.0 + 50.0 - 25.0
        self.assertIsNone(res["actual_physical_audit_stock"])
        self.assertEqual(res["inventory_shortage_variance"], 0.0)

    def test_audit_variance_checker(self):
        """Verifies manual override count trigger and shortage variance math."""
        sku = "Servo_4T_1L"
        name = "Servo 4T 1L"
        lube_stock_book.save_lube_inventory_item(
            item_sku=sku,
            item_name=name,
            opening_stock=50.0,
            inward_receipt=20.0,
            db_path=self.test_db
        )
        
        # Aggregate with 0 sales
        lube_stock_book.compute_running_lube_book(sku, "2026-06", self.test_db)
        
        # Expected closing stock is 50 + 20 - 0 = 70.0 L
        # Commit actual physical count = 65.0 L
        res = lube_stock_book.commit_physical_stock_count(sku, 65.0, self.test_db)
        
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["actual_physical_audit_stock"], 65.0)
        self.assertEqual(res["inventory_shortage_variance"], -5.0) # 65.0 - 70.0 = -5.0

    def test_excel_export_sync(self):
        """Verifies Lubricant Stock Inventory Book sheet is generated correctly inside the Excel output workbook."""
        sku = "Servo_4T_1L"
        name = "Servo 4T 1L"
        lube_stock_book.save_lube_inventory_item(
            item_sku=sku,
            item_name=name,
            opening_stock=200.0,
            inward_receipt=100.0,
            db_path=self.test_db
        )
        lube_sales.save_lube_sale("2026-06-05", name, 40.0, 350.0, 14000.0, self.test_db)
        lube_stock_book.compute_running_lube_book(sku, "2026-06", self.test_db)
        lube_stock_book.commit_physical_stock_count(sku, 255.0, self.test_db)
        
        # Trigger excel export
        exporter.export_db_to_excel(self.test_excel)
        self.assertTrue(os.path.exists(self.test_excel))
        
        # Check sheet
        excel_file = pd.ExcelFile(self.test_excel)
        self.assertIn("Lubricant Stock Inventory Book", excel_file.sheet_names)
        
        df = excel_file.parse("Lubricant Stock Inventory Book")
        self.assertFalse(df.empty)
        self.assertIn("Item SKU", df.columns)
        self.assertIn("Inventory Shortage Variance (Units)", df.columns)
        
        sku_list = df["Item SKU"].astype(str).tolist()
        self.assertIn(sku, sku_list)
        
        row_data = df[df["Item SKU"] == sku].iloc[0]
        self.assertEqual(row_data["Opening Stock (Units)"], 200.0)
        self.assertEqual(row_data["Inward Receipts (Units)"], 100.0)
        self.assertEqual(row_data["Outward Sold (Units)"], 40.0)
        self.assertEqual(row_data["Expected Closing Stock (Units)"], 260.0)
        self.assertEqual(row_data["Actual Physical Audit Stock (Units)"], 255.0)
        self.assertEqual(row_data["Inventory Shortage Variance (Units)"], -5.0)

    def test_api_endpoints(self):
        """Verifies API routes for fetching ledger, triggering rollup, and saving overrides."""
        sku = "Servo_4T_1L"
        name = "Servo 4T 1L"
        lube_stock_book.save_lube_inventory_item(sku, name, 80.0, 20.0, self.test_db)
        lube_sales.save_lube_sale("2026-06-01", name, 10.0, 300.0, 3000.0, self.test_db)
        
        # 1. Test GET /api/lube/ledger
        response_get = self.client.get("/api/lube/ledger")
        self.assertEqual(response_get.status_code, 200)
        data_get = response_get.json()
        self.assertEqual(data_get["status"], "success")
        item_skus = [x["item_sku"] for x in data_get["ledger"]]
        self.assertIn(sku, item_skus)
        
        # 2. Test POST /api/lube/reconcile
        payload_recon = {
            "item_sku": sku,
            "target_month": "2026-06"
        }
        response_recon = self.client.post("/api/lube/reconcile", json=payload_recon)
        self.assertEqual(response_recon.status_code, 200)
        data_recon = response_recon.json()
        self.assertEqual(data_recon["status"], "success")
        self.assertEqual(data_recon["expected_closing_stock"], 90.0) # 80 + 20 - 10 = 90
        
        # 3. Test POST /api/lube/physical-count
        payload_count = {
            "item_sku": sku,
            "current_physical_count": 87.0
        }
        response_count = self.client.post("/api/lube/physical-count", json=payload_count)
        self.assertEqual(response_count.status_code, 200)
        data_count = response_count.json()
        self.assertEqual(data_count["status"], "success")
        self.assertEqual(data_count["actual_physical_audit_stock"], 87.0)
        self.assertEqual(data_count["inventory_shortage_variance"], -3.0) # 87 - 90 = -3

if __name__ == "__main__":
    unittest.main()
