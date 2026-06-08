import os
import sys
import unittest
import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient

# Add backend to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

from main import app
import customer_alerts
from crypto_vault import encrypt_field
from fifo_settler import ensure_fifo_columns

class TestCustomerAlerts(unittest.TestCase):
    def setUp(self):
        # Isolate database path to prevent database leakage and deletion of dev data
        self.orig_db_path = customer_alerts.DB_PATH
        self.test_db_path = os.path.join(BACKEND_DIR, "test_ledger_alerts.db")
        customer_alerts.DB_PATH = self.test_db_path
        
        self.client = TestClient(app)
        self.db_path = self.test_db_path

        # Set up a clean database connection
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Re-initialize/ensure tables exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount TEXT,
                type TEXT,
                remarks TEXT,
                payment_status TEXT,
                amount_remaining REAL,
                linked_payment_id TEXT
            )
        """)
        cursor.execute("DELETE FROM ledger_entries")

        # Test customer data setup
        self.test_party = "Jodhpur Freight Carriers"
        self.party_enc = encrypt_field(self.test_party)

        # 1. First credit slip (oldest) - 15000.00 on 2026-06-01
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-01", self.party_enc, "RJ19-GA-1234", encrypt_field(15000.0), "udhaar", "Slip 1", "UNPAID"))

        # 2. Second credit slip - 8000.00 on 2026-06-02
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-02", self.party_enc, "RJ19-GA-5678", encrypt_field(8000.0), "udhaar", "Slip 2", "UNPAID"))

        # 3. First payment - 5000.00 on 2026-06-03
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-03", self.party_enc, "Payment", encrypt_field(5000.0), "payment", "Part payment", "N/A"))

        conn.commit()
        conn.close()

        # Setup FIFO column status
        ensure_fifo_columns(self.db_path)

    def tearDown(self):
        # Restore DB path and cleanup file
        customer_alerts.DB_PATH = self.orig_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def test_draft_outstanding_reminder(self):
        # Outstanding is: (15000 + 8000) - 5000 = 18000.00
        # Oldest unpaid slip: 15000.00 on 2026-06-01
        msg = customer_alerts.draft_outstanding_reminder(self.test_party, self.db_path)
        
        self.assertIn("Jodhpur Freight Carriers", msg)
        self.assertIn("₹18,000.00", msg) # Total outstanding formatted
        self.assertIn("2026-06-01", msg) # Oldest date
        self.assertIn("₹15,000.00", msg) # Oldest amount formatted
        self.assertIn("Aap niche diye gaye secure local link par click karke", msg)
        self.assertIn("http://", msg)
        self.assertIn("/share/ledger/", msg)

    def test_draft_outstanding_reminder_no_debts(self):
        # Setup clean state for a new customer
        empty_party = "Healthy Client Co"
        msg = customer_alerts.draft_outstanding_reminder(empty_party, self.db_path)
        
        self.assertIn("Healthy Client Co", msg)
        self.assertIn("₹0.00", msg) # Total outstanding is 0
        self.assertIn("दिनांक *N/A* ki hai (Amt: ₹0.00)", msg)

    def test_get_customer_reminder_api(self):
        # Verify valid API invocation
        resp = self.client.get(f"/api/customer/reminder?party_name={self.test_party}")
        self.assertEqual(resp.status_code, 200)
        
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("reminder", data)
        self.assertIn("Jodhpur Freight Carriers", data["reminder"])
        
        # Verify empty/invalid invocation
        resp_err = self.client.get("/api/customer/reminder?party_name=")
        self.assertEqual(resp_err.status_code, 400)

if __name__ == "__main__":
    unittest.main()
