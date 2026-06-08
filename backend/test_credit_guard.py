"""
Unit test suite for credit_guard.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil

import init_db
import crypto_vault
import credit_guard

class TestCreditGuard(unittest.TestCase):
    def setUp(self):
        # Create isolated temp database
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Save original and override init_db.DB_PATH
        self.original_init_db = init_db.DB_PATH
        init_db.DB_PATH = self.test_db
        init_db.initialize_database()
        
        # Initialize credit schema
        credit_guard.init_credit_db(self.test_db)
        
        # Configure master key for cryptography
        os.environ["PUMP_AI_MASTER_KEY"] = "test_credit_secret_key"
        crypto_vault._fernet = None

    def tearDown(self):
        # Restore original path
        init_db.DB_PATH = self.original_init_db
        
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='credit_limits'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_running_balance_aggregation(self):
        # Insert encrypted ledger rows for a customer
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Credit sale (debit) +12000.0
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-01', ?, 'N/A', ?, 'udhaar', 'HSD credit sale')
        """, (crypto_vault.encrypt_field("Gopalram Ji Dhaba"), crypto_vault.encrypt_field(12000.0)))
        
        # Credit sale (debit) +5000.0
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-02', ?, 'N/A', ?, 'udhaar', 'Petrol credit sale')
        """, (crypto_vault.encrypt_field("gopalram ji dhaba"), crypto_vault.encrypt_field(5000.0))) # Case insensitivity test
        
        # Payment received (credit) -6000.0
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-03', ?, 'N/A', ?, 'payment', 'Partial balance clear')
        """, (crypto_vault.encrypt_field("Gopalram Ji Dhaba"), crypto_vault.encrypt_field(6000.0)))
        
        # Negative debit adjustment (credit) -1000.0
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-04', ?, 'N/A', ?, 'udhaar', 'Discount adjustment')
        """, (crypto_vault.encrypt_field("Gopalram Ji Dhaba"), crypto_vault.encrypt_field(-1000.0)))
        
        # Insert a transaction for another party (should not affect balance)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES ('2026-06-04', ?, 'N/A', ?, 'udhaar', 'Other party transaction')
        """, (crypto_vault.encrypt_field("Sharma Transport"), crypto_vault.encrypt_field(4000.0)))
        
        conn.commit()
        conn.close()
        
        # Compute outstanding balance
        # Expected outstanding = 12000 + 5000 - 6000 - 1000 = 10000.0
        balance = credit_guard.get_running_customer_balance("Gopalram Ji Dhaba", db_path=self.test_db)
        self.assertEqual(balance, 10000.0)
        
        # Case insensitive test
        balance_lowercase = credit_guard.get_running_customer_balance("gopalram ji dhaba", db_path=self.test_db)
        self.assertEqual(balance_lowercase, 10000.0)

    def test_credit_limits_upsert_and_fetch(self):
        credit_guard.set_credit_limit("Gopalram Ji Dhaba", 15000.0, 75.0, db_path=self.test_db)
        
        limit_info = credit_guard.get_credit_limit("Gopalram Ji Dhaba", db_path=self.test_db)
        self.assertIsNotNone(limit_info)
        self.assertEqual(limit_info["max_allowed_udhaar"], 15000.0)
        self.assertEqual(limit_info["alert_threshold_percentage"], 75.0)
        
        # Test case-insensitive fetch
        limit_lowercase = credit_guard.get_credit_limit("gopalram ji dhaba", db_path=self.test_db)
        self.assertIsNotNone(limit_lowercase)
        self.assertEqual(limit_lowercase["max_allowed_udhaar"], 15000.0)

    def test_credit_limit_checks_and_warnings(self):
        # Configure limit of 10000.0 with 80% alert threshold
        credit_guard.set_credit_limit("Gopalram Ji Dhaba", 10000.0, 80.0, db_path=self.test_db)
        
        # 1. No transactions recorded yet. Balance = 0.
        # Adding 5000: Total = 5000 (50%). No warning.
        alert = credit_guard.check_credit_limit("Gopalram Ji Dhaba", 5000.0, db_path=self.test_db)
        self.assertIsNone(alert)
        
        # 2. Add 8500: Total = 8500 (85%). Exceeds threshold (8000), but below max (10000). Warning alert.
        alert = credit_guard.check_credit_limit("Gopalram Ji Dhaba", 8500.0, db_path=self.test_db)
        self.assertEqual(alert, "Warning: Gopalram Ji Dhaba balance has approached their credit threshold")
        
        # 3. Add 12000: Total = 12000 (120%). Exceeds cap (10000). Overdraft warning cap.
        alert = credit_guard.check_credit_limit("Gopalram Ji Dhaba", 12000.0, db_path=self.test_db)
        self.assertEqual(alert, "Warning: Gopalram Ji Dhaba balance has exceeded their credit cap")

        # 4. Check for party with no limit set (should return None)
        alert = credit_guard.check_credit_limit("Unknown Customer", 50000.0, db_path=self.test_db)
        self.assertIsNone(alert)

if __name__ == "__main__":
    unittest.main()
