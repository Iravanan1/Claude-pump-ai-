import os
import sys
import shutil
import sqlite3
import unittest
import pandas as pd

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import omc_reconciler
import decanting_auditor
import bank_matcher
import init_db

class TestOMCSupplierReconciler(unittest.TestCase):
    def setUp(self):
        # Create test sandbox
        self.test_dir = os.path.join(BACKEND_DIR, "test_omc_sandbox")
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.test_db_path = os.path.join(self.test_dir, "test_ledger.db")
        self.test_csv_path = os.path.join(self.test_dir, "portal_statement.csv")
        
        # Patch other modules to use the sandbox database path
        import price_registry
        import decanting_auditor
        import tank_calibration
        import bank_matcher
        
        self.orig_price_db = price_registry.DB_PATH
        self.orig_decant_db = decanting_auditor.DB_PATH
        self.orig_cal_db = tank_calibration.DB_PATH
        self.orig_bank_db = bank_matcher.DB_PATH
        self.orig_init_db = init_db.DB_PATH
        
        price_registry.DB_PATH = self.test_db_path
        decanting_auditor.DB_PATH = self.test_db_path
        tank_calibration.DB_PATH = self.test_db_path
        bank_matcher.DB_PATH = self.test_db_path
        
        # Initialize full database structure
        init_db.DB_PATH = self.test_db_path
        init_db.initialize_database()
        
        # Explicitly initialize dependent tables for testing
        bank_matcher.init_bank_matcher_db(self.test_db_path)
        decanting_auditor.init_decanting_db(self.test_db_path)
        tank_calibration.init_calibration_db(self.test_db_path)
        
        # Explicitly init OMC reconciler tables
        omc_reconciler.init_omc_reconciler_db(self.test_db_path)

    def tearDown(self):
        # Restore original DB paths
        import price_registry
        import decanting_auditor
        import tank_calibration
        import bank_matcher
        
        price_registry.DB_PATH = self.orig_price_db
        decanting_auditor.DB_PATH = self.orig_decant_db
        tank_calibration.DB_PATH = self.orig_cal_db
        bank_matcher.DB_PATH = self.orig_bank_db
        init_db.DB_PATH = self.orig_init_db
        
        # Clean up test sandbox
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_ledger_initialization(self):
        """Verifies that the omc_advance_ledger table structure is correctly created."""
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(omc_advance_ledger)")
        cols = [c[1] for c in cursor.fetchall()]
        conn.close()
        
        self.assertIn("transaction_date", cols)
        self.assertIn("reference_no", cols)
        self.assertIn("description", cols)
        self.assertIn("debit_amount", cols)
        self.assertIn("credit_amount", cols)
        self.assertIn("running_advance_balance", cols)

    def test_log_transaction_and_running_balance(self):
        """Asserts that logging deposits and invoice deductions computes the running balance correctly."""
        # 1. Log advance deposit
        success1 = omc_reconciler.log_omc_transaction(
            db_path=self.test_db_path,
            date_str="2026-06-01",
            reference_no="UTR_11111",
            description="ADVANCE_DEPOSIT",
            debit=0.0,
            credit=100000.0
        )
        self.assertTrue(success1)
        
        # 2. Log invoice deduction
        success2 = omc_reconciler.log_omc_transaction(
            db_path=self.test_db_path,
            date_str="2026-06-02",
            reference_no="INV_22222",
            description="INVOICE_DEDUCTION",
            debit=40000.0,
            credit=0.0
        )
        self.assertTrue(success2)
        
        # 3. Log out-of-order date deposit
        success3 = omc_reconciler.log_omc_transaction(
            db_path=self.test_db_path,
            date_str="2026-05-31",
            reference_no="UTR_00000",
            description="ADVANCE_DEPOSIT",
            debit=0.0,
            credit=50000.0
        )
        self.assertTrue(success3)
        
        # Assert running balances chronologically:
        # 2026-05-31: Deposit 50,000 -> Balance 50,000
        # 2026-06-01: Deposit 100,000 -> Balance 150,000
        # 2026-06-02: Deduction 40,000 -> Balance 110,000
        
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_date, running_advance_balance FROM omc_advance_ledger ORDER BY transaction_date ASC")
        rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "2026-05-31")
        self.assertEqual(rows[0][1], 50000.0)
        
        self.assertEqual(rows[1][0], "2026-06-01")
        self.assertEqual(rows[1][1], 150000.0)
        
        self.assertEqual(rows[2][0], "2026-06-02")
        self.assertEqual(rows[2][1], 110000.0)

    def test_decanting_connector(self):
        """Verifies that save_tanker_receipt automatically posts a debit deduction to the OMC ledger."""
        # Save a tanker receipt
        res = decanting_auditor.save_tanker_receipt(
            invoice_no="INV_TEST_99",
            date_str="2026-06-01",
            tank_lorry_no="HR-38-9999",
            product_type="HSD",
            invoice_volume_liters=12000.0,
            invoice_density_at_15c=0.835,
            observed_compartment_dips_mm="1200,1210,1190",
            observed_density_raw=0.835,
            observed_temperature_celsius=15.0,
            current_dip_mm=100.0,
            db_path=self.test_db_path
        )
        self.assertEqual(res["status"], "success")
        
        # Query OMC ledger to verify the invoice deduction was logged
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT reference_no, description, debit_amount, credit_amount FROM omc_advance_ledger WHERE reference_no = ?", ("INV_TEST_99",))
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "INV_TEST_99")
        self.assertEqual(row[1], "INVOICE_DEDUCTION")
        self.assertGreater(row[2], 0.0) # gross value computed from default HSD rate
        self.assertEqual(row[3], 0.0)

    def test_bank_statement_connector(self):
        """Verifies that save_bank_statement_credits identifies HPCL/IOCL chalan payments and logs them as deposits."""
        mock_txns = [
            {
                "bank_name": "generic",
                "transaction_date": "2026-06-01",
                "description": "UPI/HPCL ADVANCE CHALAN/9999",
                "utr_string": "UTR_CHALAN_777",
                "credit_amount": 0.0,
                "debit_amount": 150000.0
            },
            {
                "bank_name": "generic",
                "transaction_date": "2026-06-02",
                "description": "WITHDRAWAL CASH ATM",
                "utr_string": "ATM_12345",
                "credit_amount": 0.0,
                "debit_amount": 10000.0
            }
        ]
        
        # Save statement row
        inserted = bank_matcher.save_bank_statement_credits(mock_txns, db_path=self.test_db_path)
        self.assertEqual(inserted, 2)
        
        # Query OMC ledger to check that the HPCL advance deposit was logged but Cash ATM withdrawal was ignored
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT reference_no, credit_amount FROM omc_advance_ledger")
        rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "UTR_CHALAN_777")
        self.assertEqual(rows[0][1], 150000.0)

    def test_audit_omc_statement_mismatches(self):
        """Verifies that the matcher script flags uncredited deposits, overcharges, and missing invoices."""
        # 1. Populate local ledger
        # Local deposit (uncredited in portal)
        omc_reconciler.log_omc_transaction(self.test_db_path, "2026-06-01", "UTR_UNCREDITED_1", "ADVANCE_DEPOSIT", 0.0, 75000.0)
        # Local deposit (credited correctly in portal)
        omc_reconciler.log_omc_transaction(self.test_db_path, "2026-06-02", "UTR_CREDITED_2", "ADVANCE_DEPOSIT", 0.0, 120000.0)
        # Local invoice deduction (correctly charged in portal)
        omc_reconciler.log_omc_transaction(self.test_db_path, "2026-06-03", "INV_CORRECT_3", "INVOICE_DEDUCTION", 90000.0, 0.0)
        # Local invoice deduction (overcharged in portal)
        omc_reconciler.log_omc_transaction(self.test_db_path, "2026-06-04", "INV_OVERCHARGED_4", "INVOICE_DEDUCTION", 50000.0, 0.0)
        
        # 2. Write portal CSV
        # Columns date, reference_no, debit_amount, credit_amount
        portal_data = [
            {"posting_date": "2026-06-02", "ref": "UTR_CREDITED_2", "debit_amount": 0.0, "credit_amount": 120000.0},
            {"posting_date": "2026-06-03", "ref": "INV_CORRECT_3", "debit_amount": 90000.0, "credit_amount": 0.0},
            {"posting_date": "2026-06-04", "ref": "INV_OVERCHARGED_4", "debit_amount": 55000.0, "credit_amount": 0.0}, # Overcharged by 5,000
            {"posting_date": "2026-06-05", "ref": "INV_MISSING_LOCAL_5", "debit_amount": 45000.0, "credit_amount": 0.0} # Missing locally
        ]
        pd.DataFrame(portal_data).to_csv(self.test_csv_path, index=False)
        
        # 3. Audit Statement
        report = omc_reconciler.audit_omc_statement_mismatches(self.test_csv_path, self.test_db_path)
        
        # Assert uncredited deposit detected (UTR_UNCREDITED_1)
        self.assertEqual(len(report["uncredited_deposits"]), 1)
        self.assertEqual(report["uncredited_deposits"][0]["reference_no"], "UTR_UNCREDITED_1")
        self.assertEqual(report["uncredited_deposits"][0]["amount"], 75000.0)
        
        # Assert pricing overcharge detected (INV_OVERCHARGED_4)
        self.assertEqual(len(report["pricing_overcharges"]), 1)
        self.assertEqual(report["pricing_overcharges"][0]["reference_no"], "INV_OVERCHARGED_4")
        self.assertEqual(report["pricing_overcharges"][0]["local_amount"], 50000.0)
        self.assertEqual(report["pricing_overcharges"][0]["portal_amount"], 55000.0)
        self.assertEqual(report["pricing_overcharges"][0]["overcharge"], 5000.0)
        
        # Assert missing invoice detected (INV_MISSING_LOCAL_5)
        self.assertEqual(len(report["missing_invoices"]), 1)
        self.assertEqual(report["missing_invoices"][0]["reference_no"], "INV_MISSING_LOCAL_5")
        self.assertEqual(report["missing_invoices"][0]["amount"], 45000.0)

if __name__ == "__main__":
    unittest.main()
