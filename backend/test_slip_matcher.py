#!/usr/bin/env python3
"""
Comprehensive Unit Tests for slip_matcher.py.
Covers SQLite table integrity, Gemini multi-object vision parsing mocks,
alphanumeric plate normalizations, amount tolerance constraints,
fallback party name matching, and discrepancy status matrix transitions.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Make sure imports resolved from current directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_vault import encrypt_field
from slip_matcher import (
    init_slips_db,
    extract_credit_slips_from_image,
    save_extracted_slips,
    cross_reference_slips_to_ledger
)

def _fresh_db() -> str:
    """Helper to initialize a temporary database for testing."""
    tmp = tempfile.mktemp(suffix=".db")
    init_slips_db(tmp)
    # Also initialize mock ledger_entries table in same DB
    conn = sqlite3.connect(tmp)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ledger_entries (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        party_name TEXT,
        vehicle_wheel_no TEXT,
        amount TEXT, -- Encrypted at rest
        type TEXT,
        remarks TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    return tmp

class TestSlipMatcherSchema(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def test_schema_tables_exist(self):
        conn = sqlite3.connect(self.db)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='credit_slips'")
        row = cursor.fetchone()
        self.assertIsNotNone(row, "credit_slips table should be created successfully")
        
        cursor.execute("PRAGMA table_info(credit_slips)")
        cols = {col[1]: col[2] for col in cursor.fetchall()}
        self.assertIn("slip_id", cols)
        self.assertIn("date", cols)
        self.assertIn("party_name", cols)
        self.assertIn("vehicle_no", cols)
        self.assertIn("amount_or_liters", cols)
        self.assertIn("driver_signature_detected", cols)
        self.assertIn("matched_ledger_id", cols)
        conn.close()

    def test_idempotent_init(self):
        """Calling init_slips_db multiple times shouldn't cause errors."""
        init_slips_db(self.db)
        init_slips_db(self.db)

class TestGeminiVisionMock(unittest.TestCase):
    @patch("slip_matcher.genai.Client")
    @patch("slip_matcher.check_budget")
    @patch("slip_matcher.log_api_transaction")
    def test_extract_credit_slips_valid(self, mock_log, mock_budget, mock_client_class):
        # Setup mocked model response
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = """
        [
          {
            "slip_id": "SLIP-101",
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ-14-CA-5388",
            "amount_or_liters": 4500.0,
            "driver_signature_detected": true
          }
        ]
        """
        mock_client.models.generate_content.return_value = mock_response
        
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-api-key"}):
            slips = extract_credit_slips_from_image(b"fake-image-bytes")
            self.assertEqual(len(slips), 1)
            self.assertEqual(slips[0]["slip_id"], "SLIP-101")
            self.assertEqual(slips[0]["party_name"], "RJ Transport")
            self.assertEqual(slips[0]["vehicle_no"], "RJ-14-CA-5388")
            self.assertEqual(slips[0]["amount_or_liters"], 4500.0)
            self.assertTrue(slips[0]["driver_signature_detected"])

class TestCrossExaminationLogic(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def _seed_ledger(self, entries):
        conn = sqlite3.connect(self.db)
        cursor = conn.cursor()
        for e in entries:
            cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, 'udhaar', ?)
            """, (
                e["date"],
                encrypt_field(e["party_name"]),
                e["vehicle_no"],
                encrypt_field(str(e["amount"])),
                e.get("remarks", "Udhaar sale")
            ))
        conn.commit()
        conn.close()

    def test_perfect_match(self):
        # 1. Seed ledger entries
        self._seed_ledger([{
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ14CA5388",
            "amount": 4500.0
        }])
        
        # 2. Save physical slips
        slips = [{
            "slip_id": "SLIP-1",
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ-14-CA-5388",
            "amount_or_liters": 4500.0,
            "driver_signature_detected": True
        }]
        save_extracted_slips(slips, "2026-06-01", db_path=self.db)
        
        # 3. Cross-reference
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        
        self.assertEqual(report["status"], "success")
        self.assertEqual(len(report["slips"]), 1)
        self.assertEqual(len(report["ledger_entries"]), 1)
        
        self.assertEqual(report["slips"][0]["status"], "MATCHED")
        self.assertEqual(report["ledger_entries"][0]["status"], "MATCHED")
        self.assertIsNotNone(report["slips"][0]["matched_ledger_id"])

    def test_alphanumeric_vehicle_plate_normalization(self):
        """RJ-14-CA-5388 should match RJ14CA5388, rj 14 ca 5388 etc."""
        self._seed_ledger([{
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "rj 14 ca 5388",
            "amount": 3500.0
        }])
        
        slips = [{
            "slip_id": "SLIP-1",
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ-14-CA-5388",
            "amount_or_liters": 3500.0,
            "driver_signature_detected": True
        }]
        save_extracted_slips(slips, "2026-06-01", db_path=self.db)
        
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        self.assertEqual(report["slips"][0]["status"], "MATCHED")

    def test_decimal_amount_tolerance_match(self):
        """Float proximity match within 0.01 deviation."""
        self._seed_ledger([{
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ14CA5388",
            "amount": 2500.003
        }])
        
        slips = [{
            "slip_id": "SLIP-1",
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ14CA5388",
            "amount_or_liters": 2500.001,
            "driver_signature_detected": True
        }]
        save_extracted_slips(slips, "2026-06-01", db_path=self.db)
        
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        self.assertEqual(report["slips"][0]["status"], "MATCHED")

    def test_fallback_party_name_similarity_match(self):
        """Should loose match if vehicle plate doesn't align but name contains party and exact amount matches."""
        self._seed_ledger([{
            "date": "2026-06-01",
            "party_name": "Gopalram Ji Logistics",
            "vehicle_no": "NA",
            "amount": 6000.0
        }])
        
        slips = [{
            "slip_id": "SLIP-1",
            "date": "2026-06-01",
            "party_name": "Gopalram Ji",
            "vehicle_no": "",
            "amount_or_liters": 6000.0,
            "driver_signature_detected": True
        }]
        save_extracted_slips(slips, "2026-06-01", db_path=self.db)
        
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        self.assertEqual(report["slips"][0]["status"], "MATCHED")

    def test_unrecorded_slip_alert(self):
        """Physical slip is present, but no matching entry exists in register."""
        slips = [{
            "slip_id": "SLIP-999",
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ14CA5388",
            "amount_or_liters": 999.0,
            "driver_signature_detected": True
        }]
        save_extracted_slips(slips, "2026-06-01", db_path=self.db)
        
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        self.assertEqual(report["slips"][0]["status"], "UNRECORDED_SLIP_ALERT")

    def test_missing_slip_proof_alert(self):
        """Ledger entry exists, but has no corresponding physical slip scanned."""
        self._seed_ledger([{
            "date": "2026-06-01",
            "party_name": "RJ Transport",
            "vehicle_no": "RJ14CA5388",
            "amount": 7800.0
        }])
        
        report = cross_reference_slips_to_ledger("2026-06-01", db_path=self.db)
        self.assertEqual(report["ledger_entries"][0]["status"], "MISSING_SLIP_PROOF")

if __name__ == "__main__":
    unittest.main()
