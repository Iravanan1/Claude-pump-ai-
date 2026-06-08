"""
Comprehensive unit and integration tests for evaporation_handler.py.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaporation_handler import (
    calculate_evaporation_allowances,
    MS_ALLOWANCE_RATE,
    HSD_ALLOWANCE_RATE
)
from reconciliation import init_recon_db, save_reconciliation


class TestEvaporationHandler(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database for each test to ensure isolation
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        
        # Initialize tables
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT UNIQUE,
            total_hsd_liters REAL,
            total_ms_liters REAL,
            total_cash_calculated REAL,
            total_credit_sales REAL,
            total_testing_deductions REAL,
            is_verified INTEGER
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ledger (
            date TEXT UNIQUE,
            cash_tender REAL,
            upi_tender REAL,
            paytm_transfers REAL,
            card_tender REAL,
            udhaar_sales REAL
        )
        """)
        conn.commit()
        conn.close()
        
        # Initialize stock_recon table
        init_recon_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_coefficients_exist(self):
        """Verifies Indian Oil Company coefficients match regulations."""
        self.assertAlmostEqual(MS_ALLOWANCE_RATE, 0.0060)
        self.assertAlmostEqual(HSD_ALLOWANCE_RATE, 0.0020)

    def test_evaporation_no_shortage(self):
        """Verifies that surplus or zero variance yields zero evaporation loss."""
        # Insert daily summary: HSD sold = 5000L, MS sold = 3000L
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales) VALUES (?, ?, ?, ?, ?)",
            ("2026-06-01", 5000.0, 3000.0, 0.0, 0.0)
        )
        conn.commit()
        conn.close()

        # Seed reconciliation dips with positive variances (surplus / no shortage)
        # HSD expected book = 1000 + 0 - 5000 = -4000 (just for mathematical variance test)
        # Seed exact or surplus closing dip values
        save_reconciliation(
            date_str="2026-06-01",
            hsd_opening=10000.0,
            hsd_receipt=0.0,
            hsd_closing=5100.0,  # Expected book = 10000 - 5000 = 5000. Closing 5100 -> Variance = +100.0 (surplus)
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=5000.0,   # Expected book = 8000 - 3000 = 5000. Variance = 0.0 (balanced)
            db_path=self.db_path
        )

        result = calculate_evaporation_allowances("2026-06-01", db_path=self.db_path)

        # HSD variance +100 -> shortage = 0
        self.assertEqual(result["hsd_actual_shortage_liters"], 0.0)
        self.assertEqual(result["hsd_normal_evaporation_loss_liters"], 0.0)
        self.assertEqual(result["hsd_abnormal_shortage_liters"], 0.0)
        self.assertEqual(result["hsd_classification"], "No Shortage / Surplus")

        # MS variance 0 -> shortage = 0
        self.assertEqual(result["ms_actual_shortage_liters"], 0.0)
        self.assertEqual(result["ms_normal_evaporation_loss_liters"], 0.0)
        self.assertEqual(result["ms_abnormal_shortage_liters"], 0.0)
        self.assertEqual(result["ms_classification"], "No Shortage / Surplus")

    def test_evaporation_normal_loss(self):
        """Verifies shortage below permissible allowance limit is fully normal (tax deductible)."""
        # HSD sales = 10000L. Permissible loss = 10000 * 0.0020 = 20.0L
        # MS sales = 5000L. Permissible loss = 5000 * 0.0060 = 30.0L
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales) VALUES (?, ?, ?, ?, ?)",
            ("2026-06-02", 10000.0, 5000.0, 0.0, 0.0)
        )
        conn.commit()
        conn.close()

        # Actual shortage: HSD = 12.0L (<= 20.0L permissible), MS = 25.0L (<= 30.0L permissible)
        save_reconciliation(
            date_str="2026-06-02",
            hsd_opening=15000.0,
            hsd_receipt=0.0,
            hsd_closing=4988.0,  # Expected book = 15000 - 10000 = 5000. Variance = -12.0 (Shortage = 12.0L)
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=2975.0,   # Expected book = 8000 - 5000 = 3000. Variance = -25.0 (Shortage = 25.0L)
            db_path=self.db_path
        )

        result = calculate_evaporation_allowances("2026-06-02", db_path=self.db_path)

        # Assert HSD Normal Evaporation
        self.assertAlmostEqual(result["hsd_permissible_evaporation_liters"], 20.0)
        self.assertAlmostEqual(result["hsd_actual_shortage_liters"], 12.0)
        self.assertAlmostEqual(result["hsd_normal_evaporation_loss_liters"], 12.0)
        self.assertAlmostEqual(result["hsd_abnormal_shortage_liters"], 0.0)
        self.assertEqual(result["hsd_classification"], "Normal Evaporation Loss (Tax Deductible)")

        # Assert MS Normal Evaporation
        self.assertAlmostEqual(result["ms_permissible_evaporation_liters"], 30.0)
        self.assertAlmostEqual(result["ms_actual_shortage_liters"], 25.0)
        self.assertAlmostEqual(result["ms_normal_evaporation_loss_liters"], 25.0)
        self.assertAlmostEqual(result["ms_abnormal_shortage_liters"], 0.0)
        self.assertEqual(result["ms_classification"], "Normal Evaporation Loss (Tax Deductible)")

    def test_evaporation_abnormal_loss(self):
        """Verifies shortage exceeding permissible limit caps normal loss and flags abnormal shortage remainder."""
        # HSD sales = 10000L. Permissible loss = 20.0L
        # MS sales = 5000L. Permissible loss = 30.0L
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales) VALUES (?, ?, ?, ?, ?)",
            ("2026-06-03", 10000.0, 5000.0, 0.0, 0.0)
        )
        conn.commit()
        conn.close()

        # Actual shortage: HSD = 25.0L (> 20.0L limit), MS = 42.5L (> 30.0L limit)
        save_reconciliation(
            date_str="2026-06-03",
            hsd_opening=15000.0,
            hsd_receipt=0.0,
            hsd_closing=4975.0,  # Expected book = 15000 - 10000 = 5000. Variance = -25.0 (Shortage = 25.0L)
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=2957.5,   # Expected book = 8000 - 5000 = 3000. Variance = -42.5 (Shortage = 42.5L)
            db_path=self.db_path
        )

        result = calculate_evaporation_allowances("2026-06-03", db_path=self.db_path)

        # Assert HSD Normal + Abnormal
        self.assertAlmostEqual(result["hsd_permissible_evaporation_liters"], 20.0)
        self.assertAlmostEqual(result["hsd_actual_shortage_liters"], 25.0)
        self.assertAlmostEqual(result["hsd_normal_evaporation_loss_liters"], 20.0)
        self.assertAlmostEqual(result["hsd_abnormal_shortage_liters"], 5.0)
        self.assertEqual(result["hsd_classification"], "Abnormal Operational Shortage")

        # Assert MS Normal + Abnormal
        self.assertAlmostEqual(result["ms_permissible_evaporation_liters"], 30.0)
        self.assertAlmostEqual(result["ms_actual_shortage_liters"], 42.5)
        self.assertAlmostEqual(result["ms_normal_evaporation_loss_liters"], 30.0)
        self.assertAlmostEqual(result["ms_abnormal_shortage_liters"], 12.5)
        self.assertEqual(result["ms_classification"], "Abnormal Operational Shortage")


if __name__ == "__main__":
    unittest.main()
