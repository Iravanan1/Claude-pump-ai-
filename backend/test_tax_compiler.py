"""
Comprehensive unit tests for tax_compiler.py.
"""

import os
import sqlite3
import tempfile
import unittest
import shutil
import pandas as pd

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tax_compiler import (
    compile_monthly_tax_summary,
    export_tax_filing_template,
    HSD_VAT_RATE,
    MS_VAT_RATE,
    LUBE_GST_RATE
)


class TestTaxCompiler(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database and export directory
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.export_dir = tempfile.mkdtemp()
        
        # Initialize tables
        conn = sqlite3.connect(self.db_path)
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
            CREATE TABLE IF NOT EXISTS fuel_rates (
                date TEXT PRIMARY KEY,
                hsd_rate REAL,
                ms_rate REAL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory_sales (
                date TEXT,
                item_name TEXT,
                quantity_sold REAL DEFAULT 0.0,
                unit_price REAL DEFAULT 0.0,
                total_item_revenue REAL DEFAULT 0.0,
                UNIQUE(date, item_name)
            )
        """)
        
        conn.commit()
        conn.close()

    def tearDown(self):
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
            
        shutil.rmtree(self.export_dir, ignore_errors=True)

    def _seed_data(self):
        """Seeds sample fuel turnover and lubricant sales into database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. Seed daily_summary for May 2026 (two days in month, one outside)
        summaries = [
            ("2026-05-10", 1000.0, 500.0, 150000.0, 0.0, 0.0, 1),
            ("2026-05-11", 1200.0, 600.0, 180000.0, 0.0, 0.0, 1),
            ("2026-06-01", 1500.0, 800.0, 220000.0, 0.0, 0.0, 1),  # June (outside window)
        ]
        cursor.executemany("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, summaries)
        
        # 2. Seed fuel_rates
        rates = [
            ("2026-05-10", 94.0, 106.0),
            ("2026-05-11", 95.0, 107.0),
            ("2026-06-01", 96.0, 108.0),
        ]
        cursor.executemany("""
            INSERT INTO fuel_rates (date, hsd_rate, ms_rate)
            VALUES (?, ?, ?)
        """, rates)
        
        # 3. Seed inventory_sales
        lube_sales = [
            ("2026-05-10", "Servo 4T Lube 1L", 5.0, 350.0, 1750.0),
            ("2026-05-11", "Gear Oil Lube 5L", 2.0, 1500.0, 3000.0),
            ("2026-06-01", "Servo 4T Lube 1L", 4.0, 350.0, 1400.0),  # June (outside window)
        ]
        cursor.executemany("""
            INSERT INTO inventory_sales (date, item_name, quantity_sold, unit_price, total_item_revenue)
            VALUES (?, ?, ?, ?, ?)
        """, lube_sales)
        
        conn.commit()
        conn.close()

    def test_compile_monthly_tax_summary_aggregations(self):
        """Verifies tax obligations, inclusive GST splits, and unrounded decimal precision checks."""
        self._seed_data()
        
        summary = compile_monthly_tax_summary(year=2026, month=5, db_path=self.db_path)
        
        # Assert month/year matches
        self.assertEqual(summary["year"], 2026)
        self.assertEqual(summary["month"], 5)
        
        # Fuel calculations checks
        # Day 1: HSD = 1000 * 94 = 94,000. VAT = 94,000 * 0.1675 = 15745.0
        # Day 2: HSD = 1200 * 95 = 114,000. VAT = 114,000 * 0.1675 = 19095.0
        # Total HSD Liters = 2200.0
        # Total HSD Turnover = 208,000.0
        # Total HSD VAT = 34,840.0
        self.assertEqual(summary["total_hsd_liters"], 2200.0)
        self.assertEqual(summary["total_hsd_turnover"], 208000.0)
        self.assertEqual(summary["total_hsd_vat"], 34840.0)
        
        # Day 1: MS = 500 * 106 = 53,000. VAT = 53,000 * 0.1948 = 10324.4
        # Day 2: MS = 600 * 107 = 64,200. VAT = 64,200 * 0.1948 = 12506.16
        # Total MS Liters = 1100.0
        # Total MS Turnover = 117,200.0
        # Total MS VAT = 22,830.56
        self.assertEqual(summary["total_ms_liters"], 1100.0)
        self.assertEqual(summary["total_ms_turnover"], 117200.0)
        self.assertAlmostEqual(summary["total_ms_vat"], 22830.56, places=4)
        
        # Total Fuel VAT = 34,840.0 + 22,830.56 = 57,670.56
        self.assertAlmostEqual(summary["total_fuel_vat_obligation"], 57670.56, places=4)
        
        # Lubricants calculations checks
        # Sale 1: Gross = 1750.0. Base = 1750 / 1.18 = 1483.050847... CGST = SGST = 133.474576...
        # Sale 2: Gross = 3000.0. Base = 3000 / 1.18 = 2542.372881... CGST = SGST = 228.813559...
        # Total Gross = 4750.0
        # Total Base = 4750 / 1.18 = 4025.4237288...
        self.assertEqual(summary["total_lube_gross"], 4750.0)
        self.assertAlmostEqual(summary["total_lube_base"], 4025.423728813559, places=6)
        
        # Verify unrounded decimal integrity locks (floats have many decimal places, not rounded to 2 decimals)
        lube_row_1 = summary["lube_records"][0]
        self.assertTrue(len(str(lube_row_1["Base Taxable Value (INR)"]).split('.')[1]) > 4)

    def test_export_tax_filing_template_generation(self):
        """Verifies Excel dual-sheet returns template compiles successfully on disk."""
        self._seed_data()
        
        file_path = export_tax_filing_template(
            year=2026, 
            month=5, 
            db_path=self.db_path, 
            export_dir=self.export_dir
        )
        
        self.assertTrue(os.path.exists(file_path))
        self.assertEqual(os.path.basename(file_path), "Tax_Filing_Template_05.xlsx")
        
        # Open generated Excel and assert contents
        df_fuel = pd.read_excel(file_path, sheet_name="Fuel VAT Returns")
        df_lube = pd.read_excel(file_path, sheet_name="Lubricant GST GSTR-1 Data Breakdown")
        
        # Assert rows exist (data rows + 1 totals row)
        self.assertEqual(len(df_fuel), 3)  # 2 data rows + 1 totals row
        self.assertEqual(len(df_lube), 3)  # 2 data rows + 1 totals row
        
        # Verify Totals rows
        self.assertEqual(df_fuel.iloc[-1]["Date"], "TOTALS")
        self.assertAlmostEqual(df_fuel.iloc[-1]["Total VAT Obligation (INR)"], 57670.56, places=4)
        
        self.assertEqual(df_lube.iloc[-1]["Date"], "TOTALS")
        self.assertAlmostEqual(df_lube.iloc[-1]["Gross Revenue (INR)"], 4750.0, places=4)

    def test_compile_monthly_tax_summary_empty_state(self):
        """Verifies empty states aggregate zero obligation columns cleanly."""
        summary = compile_monthly_tax_summary(year=2026, month=5, db_path=self.db_path)
        self.assertEqual(summary["total_fuel_vat_obligation"], 0.0)
        self.assertEqual(summary["total_lube_gross"], 0.0)
        
        file_path = export_tax_filing_template(
            year=2026, 
            month=5, 
            db_path=self.db_path, 
            export_dir=self.export_dir
        )
        self.assertTrue(os.path.exists(file_path))


if __name__ == "__main__":
    unittest.main()
