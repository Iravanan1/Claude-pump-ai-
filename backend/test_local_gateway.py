import os
import sys
import unittest
import sqlite3
from datetime import date, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient

# Add backend to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

from main import app
import local_gateway
from crypto_vault import encrypt_field

class TestLocalGateway(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
        # Connect to test DB path or override to use a separate test setup
        self.db_path = local_gateway.DB_PATH
        
        # Populate clean test customer entries
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Ensure ledger_entries table exists and clear existing for clean state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount TEXT,
                type TEXT,
                remarks TEXT
            )
        """)
        cursor.execute("DELETE FROM ledger_entries")
        
        # Add test entries (encrypted)
        self.test_party = "Rajasthan Logistics"
        self.test_party_enc = encrypt_field(self.test_party)
        self.test_amount_enc = encrypt_field(12500.0)
        
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-06-03", self.test_party_enc, "RJ14-GB-9876", self.test_amount_enc, "udhaar", "Diesel credit"))
        
        self.test_payment_enc = encrypt_field(5000.0)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-06-03", self.test_party_enc, "RJ14-GB-9876", self.test_payment_enc, "payment", "Part payment"))
        
        conn.commit()
        conn.close()

    def test_daily_rotating_salt(self):
        # Salts on same day should be identical
        salt1 = local_gateway.get_daily_salt()
        salt2 = local_gateway.get_daily_salt()
        self.assertEqual(salt1, salt2)
        
        # Salts across different dates should rotate and be non-identical
        with patch("local_gateway.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 3)
            salt_today = local_gateway.get_daily_salt()
            
            mock_date.today.return_value = date(2026, 6, 4)
            salt_tomorrow = local_gateway.get_daily_salt()
            
        self.assertNotEqual(salt_today, salt_tomorrow)

    def test_party_name_hashing_and_resolution(self):
        # Generate hash
        p_hash = local_gateway.get_party_hash(self.test_party)
        self.assertIsNotNone(p_hash)
        self.assertEqual(len(p_hash), 64) # SHA-256 standard hexdigest size
        
        # Resolve hash back to name
        resolved = local_gateway.resolve_party_name_from_hash(p_hash)
        self.assertEqual(resolved, self.test_party)
        
        # Invalid hash should resolve to None
        invalid_resolved = local_gateway.resolve_party_name_from_hash("invalidhash123")
        self.assertIsNone(invalid_resolved)
        
        # Check case insensitivity / canonicalization matches
        p_hash_upper = local_gateway.get_party_hash("  RAJASTHAN LOGISTICS  ")
        self.assertEqual(p_hash, p_hash_upper)

    def test_api_endpoints_link_and_qr(self):
        # Test share link generator
        resp = self.client.get(f"/api/share/link?party_name={self.test_party}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("hash", data)
        self.assertIn("/share/ledger/", data["url"])
        
        # Test QR code streaming generator
        resp_qr = self.client.get(f"/api/share/qr?party_name={self.test_party}")
        self.assertEqual(resp_qr.status_code, 200)
        self.assertEqual(resp_qr.headers["content-type"], "image/png")
        self.assertTrue(len(resp_qr.content) > 100) # PNG raw bytes verification

    def test_ledger_presentation_gateway_view(self):
        # Resolve valid hash
        p_hash = local_gateway.get_party_hash(self.test_party)
        resp = self.client.get(f"/share/ledger/{p_hash}")
        self.assertEqual(resp.status_code, 200)
        
        html = resp.text
        self.assertIn("Statement of Account", html)
        self.assertIn(self.test_party, html)
        self.assertIn("RJ14-GB-9876", html)
        self.assertIn("Diesel credit", html)
        self.assertIn("12,500.00", html) # Owed amount
        self.assertIn("5,000.00", html) # Paid amount
        self.assertIn("7,500.00", html) # Outstanding balance
        
        # Test invalid hash returns 404
        resp_invalid = self.client.get("/share/ledger/somefakehashvalue")
        self.assertEqual(resp_invalid.status_code, 404)
        self.assertIn("expired or is invalid", resp_invalid.json()["detail"])

if __name__ == "__main__":
    unittest.main()
