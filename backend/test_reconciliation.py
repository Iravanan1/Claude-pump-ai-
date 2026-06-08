import os
import sys
import unittest
import sqlite3

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import reconciliation
import dsm_tracker

TEST_DB = os.path.join(BACKEND_DIR, "test_recon.db")

class TestReconciliation(unittest.TestCase):
    def setUp(self):
        # Clean up database if left over
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
            
        # Initialize test schema
        reconciliation.init_recon_db(TEST_DB)
        dsm_tracker.init_dsm_db(TEST_DB)
        
        # Populate daily_summary and daily_ledger tables in test DB for mock transactions
        conn = sqlite3.connect(TEST_DB)
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
        
        # Insert a balanced summary & ledger day
        cursor.execute("""
            INSERT INTO daily_summary (date, total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales)
            VALUES ('2026-05-30', 1000.0, 500.0, 150000.0, 30000.0)
        """)
        cursor.execute("""
            INSERT INTO daily_ledger (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales)
            VALUES ('2026-05-30', 1500.0, 150000.0, 100000.0, 20000.0, 0.0, 0.0, 30000.0)
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    def test_init_db(self):
        """Verifies table exists after initialization."""
        conn = sqlite3.connect(TEST_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_recon'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_save_and_get_reconciliation(self):
        """Tests saving and retrieving stock dip values."""
        reconciliation.save_reconciliation(
            date_str="2026-05-30",
            hsd_opening=12000.0,
            hsd_receipt=5000.0,
            hsd_closing=16000.0,
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=7500.0,
            actual_cash=98000.0,
            digital_settlements=20000.0,
            udhaar_entries=30000.0,
            db_path=TEST_DB
        )
        
        recon = reconciliation.get_reconciliation("2026-05-30", db_path=TEST_DB)
        self.assertEqual(recon["hsd_opening_dip_liters"], 12000.0)
        self.assertEqual(recon["hsd_receipt_liters"], 5000.0)
        self.assertEqual(recon["hsd_closing_dip_liters"], 16000.0)
        self.assertEqual(recon["ms_opening_dip_liters"], 8000.0)
        self.assertEqual(recon["ms_receipt_liters"], 0.0)
        self.assertEqual(recon["ms_closing_dip_liters"], 7500.0)
        self.assertEqual(recon["actual_cash_deposited"], 98000.0)
        self.assertEqual(recon["digital_wallet_settlements"], 20000.0)
        self.assertEqual(recon["logged_udhaar_entries"], 30000.0)

    def test_calculate_daily_variance_balanced(self):
        """Tests variance equations under balanced inputs."""
        reconciliation.save_reconciliation(
            date_str="2026-05-30",
            hsd_opening=12000.0,
            hsd_receipt=5000.0,
            hsd_closing=16000.0, # Expected Book: 12000 + 5000 - 1000 = 16000. Variance = 0.0
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=7500.0, # Expected Book: 8000 + 0 - 500 = 7500. Variance = 0.0
            actual_cash=100000.0,
            digital_settlements=20000.0,
            udhaar_entries=30000.0, # Reconciled Total = 150000. Calculated Sales: 150000. Difference = 0.0
            db_path=TEST_DB
        )
        
        calc = reconciliation.calculate_daily_variance("2026-05-30", db_path=TEST_DB)
        
        # Validate stock maths
        self.assertEqual(calc["expected_hsd_book_stock"], 16000.0)
        self.assertEqual(calc["hsd_variance_liters"], 0.0)
        self.assertEqual(calc["expected_ms_book_stock"], 7500.0)
        self.assertEqual(calc["ms_variance_liters"], 0.0)
        
        # Validate cash reconciliation
        self.assertEqual(calc["actual_reconciled_total"], 150000.0)
        self.assertEqual(calc["cash_short_or_over"], 0.0)
        self.assertEqual(calc["cash_status"], "balanced")

    def test_calculate_daily_variance_unbalanced(self):
        """Tests variance and shortage results under discrepant inputs."""
        reconciliation.save_reconciliation(
            date_str="2026-05-30",
            hsd_opening=12000.0,
            hsd_receipt=5000.0,
            hsd_closing=15800.0, # Expected Book: 16000. Variance: -200.0 liters shortage
            ms_opening=8000.0,
            ms_receipt=1000.0,
            ms_closing=8600.0, # Expected Book: 8000 + 1000 - 500 = 8500. Variance: +100.0 liters overage
            actual_cash=95000.0,
            digital_settlements=18000.0,
            udhaar_entries=30000.0, # Reconciled Total = 143000. Calculated: 150000. Shortage: -7000.0
            db_path=TEST_DB
        )
        
        calc = reconciliation.calculate_daily_variance("2026-05-30", db_path=TEST_DB)
        
        self.assertEqual(calc["expected_hsd_book_stock"], 16000.0)
        self.assertEqual(calc["hsd_variance_liters"], -200.0)
        self.assertEqual(calc["expected_ms_book_stock"], 8500.0)
        self.assertEqual(calc["ms_variance_liters"], 100.0)
        self.assertEqual(calc["actual_reconciled_total"], 143000.0)
        self.assertEqual(calc["cash_short_or_over"], -7000.0)
        self.assertEqual(calc["cash_status"], "shortage")
        
    def test_calculate_daily_variance_with_dsm(self):
        """Tests reconciliation cash variance cross-checking active DSM logs."""
        # 1. Setup a salesman shift showing a shortage of 120.00
        dsm_tracker.save_dsm_shift(
            date_str="2026-05-30",
            shift_type="Day",
            dsm_name="Ramesh",
            assigned_nozzles="MS-1, MS-2",
            cash_handed_over=45000.0,
            digital_slips_value=0.0,
            calculated_shortage_or_excess=-120.0,
            db_path=TEST_DB
        )
        
        # 2. Setup daily reconciliation dips and matching global cash shortage of -120.00
        # Reconciled Total = actual cash (99880.00) + digital (20000) + udhaar (30000) = 149880.00
        # Expected total calculated sales = 150000.00. Global shortage = -120.00.
        reconciliation.save_reconciliation(
            date_str="2026-05-30",
            hsd_opening=12000.0,
            hsd_receipt=5000.0,
            hsd_closing=16000.0,
            ms_opening=8000.0,
            ms_receipt=0.0,
            ms_closing=7500.0,
            actual_cash=99880.0,
            digital_settlements=20000.0,
            udhaar_entries=30000.0,
            db_path=TEST_DB
        )
        
        calc = reconciliation.calculate_daily_variance("2026-05-30", db_path=TEST_DB)
        
        # Verify global shortage
        self.assertEqual(calc["cash_short_or_over"], -120.0)
        self.assertEqual(calc["cash_status"], "shortage")
        
        # Verify DSM details
        self.assertEqual(len(calc["dsm_shifts"]), 1)
        self.assertEqual(calc["dsm_shifts"][0]["dsm_name"], "Ramesh")
        self.assertEqual(calc["total_dsm_cash_handed_over"], 45000.0)
        self.assertEqual(calc["total_dsm_shortage_or_excess"], -120.0)
        
        # Verify comparative diagnostic analysis successfully identified Ramesh
        self.assertIn("Ramesh", calc["dsm_variance_analysis"])
        self.assertIn("shortage of ₹120.00", calc["dsm_variance_analysis"])
        self.assertIn("matches the global pump shortage of ₹120.00", calc["dsm_variance_analysis"])

if __name__ == "__main__":
    unittest.main()
