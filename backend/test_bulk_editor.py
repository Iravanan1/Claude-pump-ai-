#!/usr/bin/env python3
"""
Unit Test Suite for the Bulk Ledger Editor API Endpoints.
Asserts proper querying, decryption, safe transactions, bulk updates, and excel syncing.
"""

import os
import sys
import sqlite3
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure backend directory is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
from crypto_vault import encrypt_field, decrypt_field

class TestBulkEditorAPI(unittest.TestCase):
    def setUp(self):
        """Create a temporary sandbox database and test client."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bulk.db")
        self.excel_path = os.path.join(self.test_dir, "test_ledger.xlsx")
        
        # Override paths in main
        self.original_db_path = main.DB_PATH
        self.original_excel_path = main.EXCEL_PATH
        main.DB_PATH = self.db_path
        main.EXCEL_PATH = self.excel_path
        
        # Create and initialize temporary database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount REAL DEFAULT 0.0,
                type TEXT,
                remarks TEXT,
                payment_status TEXT DEFAULT 'UNPAID',
                amount_remaining REAL DEFAULT NULL,
                linked_payment_id TEXT DEFAULT NULL,
                base_amount TEXT DEFAULT NULL,
                discount_applied TEXT DEFAULT NULL,
                base_rate TEXT DEFAULT NULL
            )
        """)
        
        # Seed test data with encrypted fields
        self.seed_entries = [
            ("2026-06-01", "Gopalram Ji", "RJ14CA5388", 3000.0, "udhaar", "HSD credit sale"),
            ("2026-06-02", "Office Expense", "N/A", 500.0, "expense", "Tea expense"),
            ("2026-06-03", "Jagveer Singh", "RJ07GB1234", 4500.0, "udhaar", "MS credit"),
        ]
        
        for entry in self.seed_entries:
            date, party, vehicle, amount, etype, remarks = entry
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                date,
                encrypt_field(party),
                vehicle,
                encrypt_field(amount),
                etype,
                remarks
            ))
            
        conn.commit()
        conn.close()
        
        self.client = TestClient(main.app)
        
    def tearDown(self):
        """Restore original paths and clean up sandbox."""
        main.DB_PATH = self.original_db_path
        main.EXCEL_PATH = self.original_excel_path
        shutil.rmtree(self.test_dir, ignore_errors=True)
        
    def test_bulk_fetch_returns_decrypted_values(self):
        """Verify that GET /api/ledger/bulk-fetch decrypts fields and respects date bounds."""
        # 1. Fetch with no bounds (returns all 3)
        res = self.client.get("/api/ledger/bulk-fetch")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        
        self.assertEqual(len(data), 3)
        
        # Verify first row
        self.assertEqual(data[0]["date"], "2026-06-01")
        self.assertEqual(data[0]["party_name"], "Gopalram Ji")
        self.assertEqual(data[0]["vehicle_wheel_no"], "RJ14CA5388")
        self.assertEqual(float(data[0]["amount"]), 3000.0)
        self.assertEqual(data[0]["type"], "udhaar")
        self.assertEqual(data[0]["remarks"], "HSD credit sale")
        
        # 2. Fetch with start_date bound
        res_bound = self.client.get("/api/ledger/bulk-fetch?start_date=2026-06-02")
        self.assertEqual(res_bound.status_code, 200)
        data_bound = res_bound.json()
        self.assertEqual(len(data_bound), 2)
        self.assertEqual(data_bound[0]["date"], "2026-06-02")
        self.assertEqual(data_bound[1]["date"], "2026-06-03")
        
    @patch("exporter.export_db_to_excel")
    def test_bulk_update_commits_transactionally(self, mock_export):
        """Verify that POST /api/ledger/bulk-update applies changes correctly and triggers sync."""
        # Retrieve original entries to get IDs
        res_fetch = self.client.get("/api/ledger/bulk-fetch")
        entries = res_fetch.json()
        
        id1 = entries[0]["entry_id"]
        id2 = entries[1]["entry_id"]
        
        # Define patches
        patches = [
            {
                "entry_id": id1,
                "party_name": "Gopalram Updated",
                "amount": 3500.0,
                "remarks": "Updated remarks"
            },
            {
                "entry_id": id2,
                "vehicle_wheel_no": "MH12AB9999",
                "type": "lube_sale"
            }
        ]
        
        # Apply patches
        res_update = self.client.post("/api/ledger/bulk-update", json=patches)
        self.assertEqual(res_update.status_code, 200)
        resp = res_update.json()
        self.assertEqual(resp["status"], "success")
        self.assertIn("Successfully updated", resp["message"])
        
        # Verify db was updated and fields are correctly encrypted/modified
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check first row
        cursor.execute("SELECT party_name, vehicle_wheel_no, amount, type, remarks FROM ledger_entries WHERE entry_id = ?", (id1,))
        row1 = cursor.fetchone()
        self.assertEqual(decrypt_field(row1[0], return_type=str), "Gopalram Updated")
        self.assertEqual(row1[1], "RJ14CA5388") # unchanged
        self.assertEqual(decrypt_field(row1[2], return_type=float), 3500.0)
        self.assertEqual(row1[3], "udhaar") # unchanged
        self.assertEqual(row1[4], "Updated remarks")
        
        # Check second row
        cursor.execute("SELECT party_name, vehicle_wheel_no, amount, type, remarks FROM ledger_entries WHERE entry_id = ?", (id2,))
        row2 = cursor.fetchone()
        self.assertEqual(decrypt_field(row2[0], return_type=str), "Office Expense") # unchanged
        self.assertEqual(row2[1], "MH12AB9999")
        self.assertEqual(decrypt_field(row2[2], return_type=float), 500.0) # unchanged
        self.assertEqual(row2[3], "lube_sale")
        self.assertEqual(row2[4], "Tea expense") # unchanged
        
        conn.close()
        
        # Assert excel synchronizer was called
        mock_export.assert_called_once()
        
    def test_bulk_update_fail_safe_rollback(self):
        """Verify that an error in any patch rolls back all updates to keep DB uncorrupted."""
        res_fetch = self.client.get("/api/ledger/bulk-fetch")
        entries = res_fetch.json()
        
        id1 = entries[0]["entry_id"]
        
        # Define patches where the second patch has an invalid amount (raises ValueError on float conversion)
        patches = [
            {
                "entry_id": id1,
                "party_name": "Rollback Test",
                "amount": 9999.0
            },
            {
                "entry_id": 99999, # non-existent or invalid row, or trigger error with invalid amount
                "amount": "not-a-float-raises-error"
            }
        ]
        
        # Execute post request, it should fail
        res_update = self.client.post("/api/ledger/bulk-update", json=patches)
        self.assertEqual(res_update.status_code, 500)
        self.assertIn("Transaction rolled back", res_update.json()["detail"])
        
        # Assert no changes were committed to SQLite (row 1 retains original Gopalram Ji / 3000.0)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, amount FROM ledger_entries WHERE entry_id = ?", (id1,))
        row = cursor.fetchone()
        conn.close()
        
        self.assertEqual(decrypt_field(row[0], return_type=str), "Gopalram Ji")
        self.assertEqual(decrypt_field(row[1], return_type=float), 3000.0)

if __name__ == "__main__":
    unittest.main()
