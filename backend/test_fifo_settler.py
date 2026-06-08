#!/usr/bin/env python3
"""
Unit Test Suite for FIFO Credit Balancing Engine.

Validates the First-In, First-Out debt settlement algorithm including:
- Column migration safety
- Full payment covering multiple debts
- Partial payment splitting a single debt
- Overpayment surplus credit advance storage
- Empty debt queue handling
- Customer status reporting
"""

import os
import sys
import sqlite3
import unittest
import tempfile
import shutil

# Ensure backend directory is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fifo_settler import (
    ensure_fifo_columns,
    allocate_realization_fifo,
    get_customer_fifo_status,
    _get_unpaid_udhaar_rows,
    _store_credit_advance,
)


class TestFIFOSettler(unittest.TestCase):
    """Isolated test harness using temporary SQLite databases."""
    
    def setUp(self):
        """Create a fresh test database with ledger_entries table and seed data."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_fifo.db")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create base ledger_entries table (without FIFO columns initially)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount REAL DEFAULT 0.0,
                type TEXT,
                remarks TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                transaction_source TEXT DEFAULT 'manual'
            )
        """)
        
        # Seed three udhaar entries for "Gopalram Ji" across different dates
        seed_entries = [
            ("2026-05-10", "Gopalram Ji", "RJ14CA5388", 3000.0, "udhaar", "HSD credit sale"),
            ("2026-05-15", "Gopalram Ji", "RJ14CA5388", 2000.0, "udhaar", "HSD credit sale"),
            ("2026-05-20", "Gopalram Ji", "RJ14CA9999", 5000.0, "udhaar", "HSD credit sale"),
        ]
        for entry in seed_entries:
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, ?, ?)
            """, entry)
        
        # Seed one entry for a different customer to verify isolation
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-05-12", "Jagveer Singh", "RJ07GB1234", 4000.0, "udhaar", "MS credit"))
        
        conn.commit()
        conn.close()
    
    def tearDown(self):
        """Clean up temporary test directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_ensure_fifo_columns(self):
        """Verify that FIFO columns are safely added to the table."""
        # Before: should not have the FIFO columns
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ledger_entries)")
        cols_before = {row[1] for row in cursor.fetchall()}
        conn.close()
        
        self.assertNotIn("payment_status", cols_before)
        self.assertNotIn("amount_remaining", cols_before)
        self.assertNotIn("linked_payment_id", cols_before)
        
        # Run migration
        ensure_fifo_columns(self.db_path)
        
        # After: columns should exist
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ledger_entries)")
        cols_after = {row[1] for row in cursor.fetchall()}
        conn.close()
        
        self.assertIn("payment_status", cols_after)
        self.assertIn("amount_remaining", cols_after)
        self.assertIn("linked_payment_id", cols_after)
        
        # Running again should be safe (idempotent)
        ensure_fifo_columns(self.db_path)
    
    def test_get_unpaid_udhaar_rows(self):
        """Verify unpaid row fetching filters by customer and sorts chronologically."""
        ensure_fifo_columns(self.db_path)
        
        rows = _get_unpaid_udhaar_rows("Gopalram Ji", self.db_path)
        
        self.assertEqual(len(rows), 3)
        # Oldest first
        self.assertEqual(rows[0]["date"], "2026-05-10")
        self.assertEqual(rows[0]["effective_outstanding"], 3000.0)
        self.assertEqual(rows[1]["date"], "2026-05-15")
        self.assertEqual(rows[2]["date"], "2026-05-20")
        
        # Other customer should be isolated
        other_rows = _get_unpaid_udhaar_rows("Jagveer Singh", self.db_path)
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows[0]["effective_outstanding"], 4000.0)
    
    def test_full_payment_covers_all_debts(self):
        """Payment of ₹10,000 should fully settle all 3 debts (₹3k + ₹2k + ₹5k)."""
        result = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=10000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_settled"], 10000.0)
        self.assertEqual(result["rows_fully_paid"], 3)
        self.assertEqual(result["rows_partially_paid"], 0)
        self.assertEqual(result["unallocated_surplus"], 0.0)
        self.assertEqual(len(result["allocated"]), 3)
        
        # Verify all rows are marked FULLY_PAID in the database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT payment_status, amount_remaining 
            FROM ledger_entries 
            WHERE type = 'udhaar' AND party_name = 'Gopalram Ji'
        """)
        statuses = cursor.fetchall()
        conn.close()
        
        for status, remaining in statuses:
            self.assertEqual(status, "FULLY_PAID")
            self.assertEqual(remaining, 0.0)
    
    def test_partial_payment_splits_row(self):
        """Payment of ₹4,000 should fully pay row 1 (₹3k) and partially pay row 2 (₹1k of ₹2k)."""
        result = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=4000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_settled"], 4000.0)
        self.assertEqual(result["rows_fully_paid"], 1)
        self.assertEqual(result["rows_partially_paid"], 1)
        self.assertEqual(result["unallocated_surplus"], 0.0)
        
        # Verify allocations
        allocs = result["allocated"]
        self.assertEqual(len(allocs), 2)
        
        # First row: fully paid
        self.assertEqual(allocs[0]["amount_settled"], 3000.0)
        self.assertEqual(allocs[0]["new_status"], "FULLY_PAID")
        self.assertEqual(allocs[0]["remaining_on_row"], 0.0)
        
        # Second row: partially paid with ₹1,000 remaining
        self.assertEqual(allocs[1]["amount_settled"], 1000.0)
        self.assertEqual(allocs[1]["new_status"], "PARTIALLY_PAID")
        self.assertEqual(allocs[1]["remaining_on_row"], 1000.0)
        
        # Verify row 3 is still untouched (UNPAID)
        unpaid = _get_unpaid_udhaar_rows("Gopalram Ji", self.db_path)
        self.assertEqual(len(unpaid), 2)  # row 2 (partial) + row 3 (unpaid)
        self.assertEqual(unpaid[0]["effective_outstanding"], 1000.0)  # row 2 remainder
        self.assertEqual(unpaid[1]["effective_outstanding"], 5000.0)  # row 3 full
    
    def test_overpayment_stores_surplus_advance(self):
        """Payment of ₹12,000 against ₹10,000 total debt should store ₹2,000 as advance."""
        result = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=12000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_settled"], 10000.0)
        self.assertEqual(result["rows_fully_paid"], 3)
        self.assertEqual(result["unallocated_surplus"], 2000.0)
        
        # Verify the advance entry was created in the database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT amount, type, remarks FROM ledger_entries WHERE type = 'advance'")
        advance_rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(advance_rows), 1)
        from crypto_vault import decrypt_field
        decrypted_amt = decrypt_field(advance_rows[0][0], return_type=float)
        self.assertEqual(decrypted_amt, 2000.0)
        self.assertIn("FIFO", advance_rows[0][2])
    
    def test_no_outstanding_debts(self):
        """Payment when no debts exist should store everything as surplus advance."""
        result = allocate_realization_fifo(
            party_name="Unknown Customer",
            payment_amount=5000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_settled"], 0.0)
        self.assertEqual(result["unallocated_surplus"], 5000.0)
        self.assertEqual(len(result["allocated"]), 0)
    
    def test_zero_payment_rejected(self):
        """Payment of ₹0 or negative should return an error status."""
        result = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=0.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        self.assertEqual(result["status"], "error")
        self.assertIn("positive", result["message"])
    
    def test_sequential_partial_payments(self):
        """Two sequential partial payments should settle debts progressively."""
        # First payment: ₹4,000 → fully pays row 1 (₹3k), partially pays row 2 (₹1k/₹2k)
        result1 = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=4000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        self.assertEqual(result1["total_settled"], 4000.0)
        
        # Second payment: ₹3,000 → settles row 2 remainder (₹1k), fully pays ₹2k of row 3
        result2 = allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=3000.0,
            payment_date="2026-06-01",
            db_path=self.db_path
        )
        self.assertEqual(result2["total_settled"], 3000.0)
        self.assertEqual(result2["rows_fully_paid"], 1)  # row 2 fully cleared
        self.assertEqual(result2["rows_partially_paid"], 1)  # row 3 partially cleared
        
        # Remaining outstanding should be ₹3,000 (row 3: ₹5k - ₹2k)
        unpaid = _get_unpaid_udhaar_rows("Gopalram Ji", self.db_path)
        self.assertEqual(len(unpaid), 1)
        self.assertEqual(unpaid[0]["effective_outstanding"], 3000.0)
    
    def test_customer_fifo_status(self):
        """Verify the FIFO status report correctly summarizes account state."""
        # Settle part of the debt first
        allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=3000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        status = get_customer_fifo_status("Gopalram Ji", self.db_path)
        
        self.assertEqual(status["party_name"], "Gopalram Ji")
        self.assertEqual(status["total_outstanding"], 7000.0)  # ₹2k + ₹5k remaining
        self.assertEqual(status["pending_debt_rows"], 2)
        self.assertEqual(status["fully_settled_rows"], 1)
    
    def test_customer_isolation(self):
        """Settling one customer's debts should not affect another customer."""
        # Settle Gopalram Ji
        allocate_realization_fifo(
            party_name="Gopalram Ji",
            payment_amount=10000.0,
            payment_date="2026-05-31",
            db_path=self.db_path
        )
        
        # Jagveer Singh should remain untouched
        unpaid = _get_unpaid_udhaar_rows("Jagveer Singh", self.db_path)
        self.assertEqual(len(unpaid), 1)
        self.assertEqual(unpaid[0]["effective_outstanding"], 4000.0)


if __name__ == "__main__":
    unittest.main()
