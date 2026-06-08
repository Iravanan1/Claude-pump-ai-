"""
Unit test suite for lube_sales.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil
import json

import crypto_vault
import lube_sales

class TestLubeSales(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Configure master key for cryptography
        os.environ["PUMP_AI_MASTER_KEY"] = "test_lube_secret_key"
        crypto_vault._fernet = None
        
        # Initialize database schema
        lube_sales.init_lube_db(self.test_db)

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        crypto_vault._fernet = None

    def test_init_db_creates_table(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inventory_sales'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_save_and_retrieve_lube_sales(self):
        # Save lube sales entries
        lube_sales.save_lube_sale(
            date_str="2026-06-01",
            item_name="Servo 4T 1L",
            quantity_sold=3.0,
            unit_price=350.0,
            total_item_revenue=1050.0,
            db_path=self.test_db
        )
        lube_sales.save_lube_sale(
            date_str="2026-06-01",
            item_name="Coolant",
            quantity_sold=1.0,
            unit_price=250.0,
            total_item_revenue=250.0,
            db_path=self.test_db
        )
        
        sales = lube_sales.get_lube_sales_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(sales), 2)
        
        # Verify fields - alphabetical order: "Coolant" (C) comes before "Servo 4T 1L" (S)
        self.assertEqual(sales[0]["item_name"], "Coolant")
        self.assertEqual(sales[0]["quantity_sold"], 1.0)
        self.assertEqual(sales[0]["unit_price"], 250.0)
        self.assertEqual(sales[0]["total_item_revenue"], 250.0)
        
        self.assertEqual(sales[1]["item_name"], "Servo 4T 1L")
        self.assertEqual(sales[1]["quantity_sold"], 3.0)
        self.assertEqual(sales[1]["unit_price"], 350.0)
        self.assertEqual(sales[1]["total_item_revenue"], 1050.0)
        
        # Verify UNIQUE constraint triggers REPLACE
        lube_sales.save_lube_sale(
            date_str="2026-06-01",
            item_name="Servo 4T 1L",
            quantity_sold=4.0, # Updated quantity
            unit_price=350.0,
            total_item_revenue=1400.0, # Updated revenue
            db_path=self.test_db
        )
        
        sales_updated = lube_sales.get_lube_sales_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(sales_updated), 2)
        # S (Servo) is still at index 1
        self.assertEqual(sales_updated[1]["quantity_sold"], 4.0)
        self.assertEqual(sales_updated[1]["total_item_revenue"], 1400.0)

    def test_delete_lube_sales_by_date(self):
        lube_sales.save_lube_sale("2026-06-01", "Servo 4T", 2.0, 350.0, 700.0, db_path=self.test_db)
        lube_sales.save_lube_sale("2026-06-02", "Coolant", 1.0, 250.0, 250.0, db_path=self.test_db)
        
        lube_sales.delete_lube_sales_by_date("2026-06-01", db_path=self.test_db)
        
        self.assertEqual(len(lube_sales.get_lube_sales_by_date("2026-06-01", db_path=self.test_db)), 0)
        self.assertEqual(len(lube_sales.get_lube_sales_by_date("2026-06-02", db_path=self.test_db)), 1)

    def test_verify_inventory_totals(self):
        # Create necessary mock tables
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
                total_amount_inr REAL,
                raw_data TEXT
            )
        """)
        
        # Populate fuel summary: total_cash_calculated = 150000.0 (Base Fuel expected)
        cursor.execute("""
            INSERT INTO daily_summary (date, total_cash_calculated) VALUES ('2026-06-10', 150000.0)
        """)
        
        # Populate raw decrypted nozzles with amount calculations
        cursor.execute("INSERT INTO daily_ledger (date, total_amount_inr) VALUES ('2026-06-10', 150000.0)")
        conn.commit()
        conn.close()
        
        # Insert lube sales: sum = 700 + 250 = 950.0
        lube_sales.save_lube_sale("2026-06-10", "Servo 4T", 2.0, 350.0, 700.0, db_path=self.test_db)
        lube_sales.save_lube_sale("2026-06-10", "Coolant", 1.0, 250.0, 250.0, db_path=self.test_db)
        
        # Run the Inventory Validation Guard
        # Expected new expected cash = 150000 (fuel) + 950 (lube) = 150950.0
        lube_sum = lube_sales.verify_inventory_totals("2026-06-10", db_path=self.test_db)
        self.assertEqual(lube_sum, 950.0)
        
        # Read from daily_summary to assert total expected calculations were updated correctly
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT total_cash_calculated FROM daily_summary WHERE date = '2026-06-10'")
        updated_cash = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(updated_cash, 150950.0)

if __name__ == "__main__":
    unittest.main()
