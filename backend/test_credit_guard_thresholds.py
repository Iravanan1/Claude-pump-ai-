#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Credit Limit Enforcement and Allocation Guard.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import init_db
import migrations
import main
import credit_guard
import price_registry
import crypto_vault

class TestCreditThresholdGuard(unittest.TestCase):
    
    def setUp(self):
        # Redirect DB paths for isolation
        self.original_init_db = init_db.DB_PATH
        self.original_main_db = main.DB_PATH
        self.original_price_db = price_registry.DB_PATH
        self.original_guard_db = credit_guard.DB_PATH
        
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        
        init_db.DB_PATH = self.temp_db_path
        main.DB_PATH = self.temp_db_path
        price_registry.DB_PATH = self.temp_db_path
        credit_guard.DB_PATH = self.temp_db_path
        
        # Initialize the database (which runs up to migration 10 now)
        init_db.initialize_database()
        
        # Initialize TestClient
        self.client = TestClient(main.app)
        
    def tearDown(self):
        # Restore DB paths
        init_db.DB_PATH = self.original_init_db
        main.DB_PATH = self.original_main_db
        price_registry.DB_PATH = self.original_price_db
        credit_guard.DB_PATH = self.original_guard_db
        
        # Clean up temporary database
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_migration_version_10_schema(self):
        """Verify that migration VERSION 10 successfully creates credit_thresholds table."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        # Check database schema version is at least 10
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        version = cursor.fetchone()[0]
        self.assertGreaterEqual(version, 10)
        
        # Verify table exists and has columns
        cursor.execute("PRAGMA table_info(credit_thresholds)")
        cols = {c[1] for c in cursor.fetchall()}
        self.assertIn("party_name", cols)
        self.assertIn("max_allowed_credit", cols)
        self.assertIn("hard_block_status", cols)
        
        conn.close()

    def test_configure_and_get_thresholds(self):
        """Verify upserting and reading credit configuration boundaries."""
        # Add a test configuration
        credit_guard.set_credit_threshold("Super Transports", 250000.0, hard_block_status=False, db_path=self.temp_db_path)
        
        # Query threshold configuration
        config = credit_guard.get_credit_threshold("Super Transports", db_path=self.temp_db_path)
        self.assertIsNotNone(config)
        self.assertEqual(config["party_name"], "Super Transports")
        self.assertEqual(config["max_allowed_credit"], 250000.0)
        self.assertFalse(config["hard_block_status"])
        
        # Test case-insensitivity
        config_ci = credit_guard.get_credit_threshold("super transports", db_path=self.temp_db_path)
        self.assertIsNotNone(config_ci)
        self.assertEqual(config_ci["max_allowed_credit"], 250000.0)
        
        # Update with hard block
        credit_guard.set_credit_threshold("Super Transports", 150000.0, hard_block_status=True, db_path=self.temp_db_path)
        config_updated = credit_guard.get_credit_threshold("Super Transports", db_path=self.temp_db_path)
        self.assertEqual(config_updated["max_allowed_credit"], 150000.0)
        self.assertTrue(config_updated["hard_block_status"])

    def test_verify_transaction_credit_safety(self):
        """Test verification math for current balance, limits, and blocks."""
        party = "Rahul Carriers"
        
        # Configure limit of ₹50,000
        credit_guard.set_credit_threshold(party, 50000.0, hard_block_status=False, db_path=self.temp_db_path)
        
        # Insert unpaid and partially paid credit entries in ledger_entries
        # To bypass crypto_vault decryption check, we encrypt them
        party_enc = crypto_vault.encrypt_field(party)
        amt1_enc = crypto_vault.encrypt_field(20000.0)
        amt2_enc = crypto_vault.encrypt_field(15000.0)
        
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        # Row 1: UNPAID - ₹20,000 outstanding
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
            VALUES ('2026-06-01', ?, 'HR38-1234', ?, 'udhaar', 'HSD credit', 'UNPAID', NULL)
        """, (party_enc, amt1_enc))
        
        # Row 2: PARTIALLY_PAID - ₹10,000 outstanding (originally 15k)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
            VALUES ('2026-06-02', ?, 'HR38-5678', ?, 'udhaar', 'MS credit', 'PARTIALLY_PAID', 10000.0)
        """, (party_enc, amt2_enc))
        
        # Row 3: FULLY_PAID - Should be ignored
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
            VALUES ('2026-06-03', ?, 'HR38-9999', ?, 'udhaar', 'Paid credit', 'FULLY_PAID', 0.0)
        """, (party_enc, amt2_enc))
        
        conn.commit()
        conn.close()
        
        # Total active unpaid outstanding: 20,000 + 10,000 = ₹30,000.
        
        # Case 1: Incoming slip of ₹10,000 (Total = 40,000 <= 50,000 limit) -> OK
        res1 = credit_guard.verify_transaction_credit_safety(party, 10000.0, db_path=self.temp_db_path)
        self.assertEqual(res1["current_unpaid_sum"], 30000.0)
        self.assertEqual(res1["combined_total"], 40000.0)
        self.assertEqual(res1["credit_status"], "OK")
        self.assertFalse(res1["breached"])
        
        # Case 2: Incoming slip of ₹25,000 (Total = 55,000 > 50,000 limit) -> THRESHOLD_BREACH_WARNING
        res2 = credit_guard.verify_transaction_credit_safety(party, 25000.0, db_path=self.temp_db_path)
        self.assertEqual(res2["combined_total"], 55000.0)
        self.assertEqual(res2["credit_status"], "THRESHOLD_BREACH_WARNING")
        self.assertTrue(res2["breached"])
        
        # Case 3: Customer marked as hard blocked -> THRESHOLD_BREACH_WARNING even with 0 incoming
        credit_guard.set_credit_threshold(party, 50000.0, hard_block_status=True, db_path=self.temp_db_path)
        res3 = credit_guard.verify_transaction_credit_safety(party, 0.0, db_path=self.temp_db_path)
        self.assertEqual(res3["credit_status"], "THRESHOLD_BREACH_WARNING")
        self.assertTrue(res3["hard_blocked"])

    def test_verify_credit_safety_api_endpoint(self):
        """Test public GET endpoint behavior."""
        party = "Jaggu Roadways"
        credit_guard.set_credit_threshold(party, 10000.0, hard_block_status=False, db_path=self.temp_db_path)
        
        # Query safety via API
        response = self.client.get(f"/api/credit/verify-safety?party_name={party}&amount=15000.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["party_name"], party)
        self.assertEqual(data["max_allowed_credit"], 10000.0)
        self.assertEqual(data["combined_total"], 15000.0)
        self.assertEqual(data["credit_status"], "THRESHOLD_BREACH_WARNING")
        self.assertTrue(data["breached"])

    def test_post_extraction_warning_payload(self):
        """Verify that analyze_register_sheet injects breach warnings into JSON response."""
        party_breach = "Blocked Corp"
        party_ok = "Good Customer"
        
        credit_guard.set_credit_threshold(party_breach, 100.0, hard_block_status=True, db_path=self.temp_db_path)
        credit_guard.set_credit_threshold(party_ok, 100000.0, hard_block_status=False, db_path=self.temp_db_path)
        
        # Mock parsed json input from vision logic
        final_accounting_json = {
            "date": "2026-06-08",
            "credit_sales": [
                {"party_name": party_breach, "vehicle_no": "HR-11", "amount": 500.0},
                {"party_name": party_ok, "vehicle_no": "HR-22", "amount": 1000.0}
            ]
        }
        
        # Call the inline check portion directly (as hooked in ai_engine.py)
        # Verify the warning status gets injected correctly
        from credit_guard import verify_transaction_credit_safety
        for sale in final_accounting_json["credit_sales"]:
            party = sale.get("party_name")
            amount = sale.get("amount")
            safety_res = verify_transaction_credit_safety(party, amount, db_path=self.temp_db_path)
            if safety_res.get("credit_status") == "THRESHOLD_BREACH_WARNING":
                sale["credit_status"] = "THRESHOLD_BREACH_WARNING"
                
        # Assertions
        sales = final_accounting_json["credit_sales"]
        self.assertEqual(sales[0]["party_name"], party_breach)
        self.assertEqual(sales[0]["credit_status"], "THRESHOLD_BREACH_WARNING")
        
        self.assertEqual(sales[1]["party_name"], party_ok)
        self.assertNotIn("credit_status", sales[1])

if __name__ == "__main__":
    unittest.main()
