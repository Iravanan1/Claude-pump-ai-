"""
Comprehensive unit and integration tests for credit_realization.py.
"""

import os
import sqlite3
import tempfile
import unittest
import shutil

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from credit_realization import (
    init_realization_db,
    save_credit_realization,
    get_all_realizations
)
from crypto_vault import decrypt_field
from main import app, DB_PATH
from fastapi.testclient import TestClient


class TestCreditRealization(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        init_realization_db(self.db_path)
        
        # Initialize ledger_entries table
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                party_name TEXT NOT NULL,
                vehicle_wheel_no TEXT,
                amount TEXT NOT NULL,
                type TEXT NOT NULL,
                remarks TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # Setup FastAPI TestClient
        self.client = TestClient(app)
        
        # Backup production DB_PATH and override it to use our temp DB in main FastAPI instance
        import main
        self._orig_db_path = main.DB_PATH
        main.DB_PATH = self.db_path

    def tearDown(self):
        # Restore production DB_PATH
        import main
        main.DB_PATH = self._orig_db_path
        
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_init_realization_db(self):
        """Verifies credit_realizations table is initialized cleanly."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(credit_realizations)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()
        
        self.assertIn("realization_id", columns)
        self.assertIn("date", columns)
        self.assertIn("party_name", columns)
        self.assertIn("amount_received", columns)
        self.assertIn("payment_mode", columns)
        self.assertIn("bank_utr_or_remarks", columns)
        self.assertIn("linked_invoice_no", columns)

    def test_save_credit_realization_stores_encrypted_and_updates_ledger(self):
        """Verifies customer balance realized commits both log details and reduction ledger entries."""
        rid = save_credit_realization(
            date_str="2026-06-01",
            party_name="Gopalram Ji Dhaba",
            amount_received=15000.0,
            payment_mode="BANK_TRANSFER",
            bank_utr_or_remarks="SBIUTR12345",
            linked_invoice_no="INV1001",
            db_path=self.db_path
        )
        
        self.assertTrue(rid > 0)
        
        # Verify realizations database contains encrypted fields
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, amount_received, payment_mode, bank_utr_or_remarks FROM credit_realizations")
        row_real = cursor.fetchone()
        
        self.assertIsNotNone(row_real)
        # Verify encryption
        self.assertNotEqual(row_real[0], "Gopalram Ji Dhaba")
        self.assertNotEqual(row_real[1], "15000.0")
        self.assertEqual(decrypt_field(row_real[0], return_type=str), "Gopalram Ji Dhaba")
        self.assertEqual(decrypt_field(row_real[1], return_type=float), 15000.0)
        self.assertEqual(row_real[2], "BANK_TRANSFER")
        self.assertEqual(row_real[3], "SBIUTR12345")
        
        # Verify that a matching 'payment' ledger entry was inserted in ledger_entries
        cursor.execute("SELECT party_name, vehicle_wheel_no, amount, type, remarks FROM ledger_entries")
        row_ledger = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row_ledger)
        self.assertEqual(decrypt_field(row_ledger[0], return_type=str), "Gopalram Ji Dhaba")
        self.assertEqual(row_ledger[1], "Payment")
        self.assertEqual(decrypt_field(row_ledger[2], return_type=float), 15000.0)
        self.assertEqual(row_ledger[3], "payment")
        self.assertIn("BANK_TRANSFER", row_ledger[4])
        self.assertIn("SBIUTR12345", row_ledger[4])
        self.assertIn("INV1001", row_ledger[4])

    def test_get_all_realizations_returns_decrypted_rows(self):
        """Verifies querying logs returns fully decrypted records."""
        save_credit_realization(
            date_str="2026-06-01",
            party_name="Jagveer Ji Dhaba",
            amount_received=8000.0,
            payment_mode="CASH",
            bank_utr_or_remarks="balance clear",
            db_path=self.db_path
        )
        
        list_reals = get_all_realizations(db_path=self.db_path)
        self.assertEqual(len(list_reals), 1)
        
        real = list_reals[0]
        self.assertEqual(real["party_name"], "Jagveer Ji Dhaba")
        self.assertEqual(real["amount_received"], 8000.0)
        self.assertEqual(real["payment_mode"], "CASH")
        self.assertEqual(real["bank_utr_or_remarks"], "balance clear")

    def test_api_credit_realizations_endpoints(self):
        """Verifies GET and POST /api/credit-realizations REST API routers."""
        # 1. Post a new realization
        payload = {
            "date": "2026-06-02",
            "party_name": "Sher-e-Punjab Dhaba",
            "amount_received": 25000.0,
            "payment_mode": "UPI",
            "bank_utr_or_remarks": "PaytmSBI",
            "linked_invoice_no": "INV1002"
        }
        response_post = self.client.post("/api/credit-realizations", json=payload)
        self.assertEqual(response_post.status_code, 200)
        
        data_post = response_post.json()
        self.assertEqual(data_post["status"], "success")
        self.assertTrue(data_post["realization_id"] > 0)
        
        # 2. Get list of realizations
        response_get = self.client.get("/api/credit-realizations")
        self.assertEqual(response_get.status_code, 200)
        
        data_get = response_get.json()
        self.assertEqual(data_get["status"], "success")
        self.assertEqual(len(data_get["realizations"]), 1)
        
        real = data_get["realizations"][0]
        self.assertEqual(real["party_name"], "Sher-e-Punjab Dhaba")
        self.assertEqual(real["amount_received"], 25000.0)
        self.assertEqual(real["payment_mode"], "UPI")
        self.assertEqual(real["bank_utr_or_remarks"], "PaytmSBI")


if __name__ == "__main__":
    unittest.main()
