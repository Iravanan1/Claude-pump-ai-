#!/usr/bin/env python3
"""
Comprehensive Unit Tests for corporate fleet card transaction reconciliation.
Validates table schemas, ingestion parsers, time-delta algorithms, encryption handling, and FastAPI endpoints.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime as dt
from fastapi.testclient import TestClient

# Ensure imports resolve relative to this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_vault import encrypt_field
from fleet_cards import (
    init_fleet_cards_db,
    clean_vehicle_no,
    import_fleet_portal_csv,
    reconcile_fleet_transactions,
    get_fleet_reconciliation_status
)
import init_db
import main
from main import app

def _fresh_db() -> str:
    """Helper to initialize a temporary database for testing."""
    tmp = tempfile.mktemp(suffix=".db")
    init_fleet_cards_db(tmp)
    
    # Also initialize dummy ledger_entries table
    conn = sqlite3.connect(tmp)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ledger_entries (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        party_name TEXT,
        vehicle_wheel_no TEXT,
        amount REAL DEFAULT 0.0,
        type TEXT, -- 'udhaar', etc.
        remarks TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    return tmp

class TestFleetCardsSchema(unittest.TestCase):
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
        
        # Verify fleet_card_sales exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fleet_card_sales'")
        self.assertIsNotNone(cursor.fetchone(), "fleet_card_sales table should exist")
        
        # Verify fleet_portal_transactions exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fleet_portal_transactions'")
        self.assertIsNotNone(cursor.fetchone(), "fleet_portal_transactions table should exist")
        
        # Check column types in fleet_card_sales
        cursor.execute("PRAGMA table_info(fleet_card_sales)")
        cols = {col[1]: col[2] for col in cursor.fetchall()}
        self.assertIn("date", cols)
        self.assertIn("card_program_name", cols)
        self.assertIn("portal_match_status", cols)
        
        conn.close()

    def test_vehicle_no_cleaning(self):
        self.assertEqual(clean_vehicle_no("RJ 14-GA 1234"), "RJ14GA1234")
        self.assertEqual(clean_vehicle_no("mh 12.xx 9999"), "MH12XX9999")
        self.assertEqual(clean_vehicle_no(None), "")

class TestFleetCardsIngestion(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def test_csv_parser_with_flexible_headers(self):
        # Create a mock CSV statement representing an oil portal sheet
        csv_data = (
            "Transaction Date/Time,Card Number,Vehicle Number,Volume,Value\n"
            "2026-06-01 10:15:30,9876543210,RJ14GA1234,45.5,4289.29\n"
            "2026-06-01 10:20:45,9876543211,MH12XX9999,35.0,3299.45\n"
        )
        
        tmp_csv = tempfile.mktemp(suffix=".csv")
        with open(tmp_csv, "w") as f:
            f.write(csv_data)
            
        try:
            imported = import_fleet_portal_csv(tmp_csv, provider="IOCL", db_path=self.db)
            self.assertEqual(imported, 2)
            
            # Query back
            conn = sqlite3.connect(self.db)
            cursor = conn.cursor()
            cursor.execute("SELECT card_number, vehicle_no, value, provider FROM fleet_portal_transactions")
            rows = cursor.fetchall()
            conn.close()
            
            self.assertEqual(rows[0][0], "9876543210")
            self.assertEqual(rows[0][1], "RJ14GA1234")
            self.assertEqual(rows[0][2], 4289.29)
            self.assertEqual(rows[0][3], "IOCL")
        finally:
            try:
                os.unlink(tmp_csv)
            except OSError:
                pass

class TestFleetReconciliationEngine(unittest.TestCase):
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
            enc_party = encrypt_field(e["party_name"])
            enc_amount = encrypt_field(e["amount"])
            cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (e["date"], enc_party, e["vehicle_no"], enc_amount, "udhaar", e["remarks"], e["created_at"]))
        conn.commit()
        conn.close()

    def _seed_portal_statement(self, txs):
        conn = sqlite3.connect(self.db)
        cursor = conn.cursor()
        for t in txs:
            cursor.execute("""
            INSERT INTO fleet_portal_transactions (transaction_datetime, card_number, vehicle_no, volume, value, provider, matched)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (t["datetime"], t["card_no"], t["vehicle_no"], t["volume"], t["value"], t["provider"]))
        conn.commit()
        conn.close()

    def test_reconciliation_exact_and_margin_match(self):
        # 1. Seed ledger with fleet cards
        self._seed_ledger([
            # Exact match vehicle + amount, time inside +/- 5m
            {
                "date": "2026-06-01",
                "party_name": "IOCL Fleet Account",
                "vehicle_no": "RJ-14-GA-1234",
                "amount": 5000.0,
                "remarks": "Time 11:20",
                "created_at": "2026-06-01 11:21:00"
            },
            # Missing in portal
            {
                "date": "2026-06-01",
                "party_name": "HPCL DriveTrack Customer",
                "vehicle_no": "MH-12-XX-9999",
                "amount": 3500.0,
                "remarks": "HPCL test",
                "created_at": "2026-06-01 11:45:00"
            },
            # Unauthorized Swipe: same amount and card-type-ish date, but vehicle differs completely
            {
                "date": "2026-06-01",
                "party_name": "IOCL Fleet Account",
                "vehicle_no": "UP-16-AA-5555",
                "amount": 2500.0,
                "remarks": "Swipe without slip time 12:00",
                "created_at": "2026-06-01 12:01:00"
            }
        ])

        # 2. Seed portalstatement
        self._seed_portal_statement([
            # Matches the first ledger entry (time matches 11:20 vs 11:18, diff is 2 mins)
            {
                "datetime": "2026-06-01 11:18:00",
                "card_no": "11112222",
                "vehicle_no": "RJ14GA1234",
                "volume": 53.0,
                "value": 4999.5, # Value is within +/- 1.0 margin
                "provider": "IOCL"
            },
            # matches the unauthorized swipe (amount is 2500, but vehicle is completely different, e.g. UP16XX0000)
            {
                "datetime": "2026-06-01 12:00:00",
                "card_no": "33334444",
                "vehicle_no": "DL01AA0000",
                "volume": 26.5,
                "value": 2500.0,
                "provider": "IOCL"
            }
        ])

        # 3. Run reconciliation
        res = reconcile_fleet_transactions("2026-06-01", db_path=self.db)
        self.assertEqual(res["total_analyzed"], 3)
        self.assertEqual(res["results"]["matched"], 1)
        self.assertEqual(res["results"]["missing_in_portal"], 1)
        self.assertEqual(res["results"]["unauthorized_swipe"], 1)

        # 4. Check status retrieval
        records = get_fleet_reconciliation_status("2026-06-01", db_path=self.db)
        self.assertEqual(len(records), 3)
        
        statuses = {r["vehicle_no"]: r["portal_match_status"] for r in records}
        self.assertEqual(statuses["RJ-14-GA-1234"], "MATCHED")
        self.assertEqual(statuses["MH-12-XX-9999"], "MISSING_IN_PORTAL")
        self.assertEqual(statuses["UP-16-AA-5555"], "UNAUTHORIZED_SWIPE_ALERT")

    def test_reconciliation_time_delta_out_of_bounds(self):
        # Time difference is more than 5 minutes (+/- 300 seconds)
        self._seed_ledger([
            {
                "date": "2026-06-01",
                "party_name": "IOCL Fleet Account",
                "vehicle_no": "RJ-14-GA-1234",
                "amount": 5000.0,
                "remarks": "Time 11:20",
                "created_at": "2026-06-01 11:20:00"
            }
        ])
        
        self._seed_portal_statement([
            # Diff is 7 minutes (greater than 5 mins limit)
            {
                "datetime": "2026-06-01 11:27:00",
                "card_no": "11112222",
                "vehicle_no": "RJ14GA1234",
                "volume": 53.0,
                "value": 5000.0,
                "provider": "IOCL"
            }
        ])

        reconcile_fleet_transactions("2026-06-01", db_path=self.db)
        records = get_fleet_reconciliation_status("2026-06-01", db_path=self.db)
        # Should be flagged as MISSING_IN_PORTAL because time difference was too high
        self.assertEqual(records[0]["portal_match_status"], "MISSING_IN_PORTAL")

class TestFleetCardsEndpoints(unittest.TestCase):
    def setUp(self):
        # Save original db path
        self.original_db = main.DB_PATH
        self.original_init_db = init_db.DB_PATH
        
        self.test_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fleet_ledger.db")
        
        main.DB_PATH = self.test_db
        init_db.DB_PATH = self.test_db
        
        # Clean up files if they exist
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
            
        # Re-initialize database
        init_db.initialize_database()
        init_fleet_cards_db(self.test_db)
        
        # Setup TestClient
        self.client = TestClient(app)

    def tearDown(self):
        # Restore original paths
        main.DB_PATH = self.original_db
        init_db.DB_PATH = self.original_init_db
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_fleet_endpoints_responsive(self):
        # Test status endpoint returns successfully
        res = self.client.get("/api/fleet/status?date=2026-06-01")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

        # Test reconcile endpoint returns successfully
        res = self.client.post("/api/fleet/reconcile?date=2026-06-01")
        self.assertEqual(res.status_code, 200)

if __name__ == "__main__":
    unittest.main()
