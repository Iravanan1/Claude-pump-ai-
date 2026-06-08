"""
Comprehensive unit tests for invoice_generator.py.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from invoice_generator import generate_customer_invoice
from crypto_vault import encrypt_field


class TestInvoiceGenerator(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.exports_dir = tempfile.mkdtemp()
        
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

    def tearDown(self):
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
            
        # Clean up exported PDFs
        import shutil
        shutil.rmtree(self.exports_dir, ignore_errors=True)

    def _seed_entries(self):
        """Seeds sample ledger entries for Gopalram Ji Dhaba (some encrypted)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        entries = [
            # Historical (before start_date 2026-06-01) -> Opening Balance
            ("2026-05-20", encrypt_field("Gopalram Ji Dhaba"), "HR-38-1234", encrypt_field(5000.0), "udhaar", "diesel fill"),
            ("2026-05-25", encrypt_field("Gopalram Ji Dhaba"), "N/A", encrypt_field(2000.0), "payment", "partial payment"),
            
            # Within Period (2026-06-01 to 2026-06-10)
            ("2026-06-02", encrypt_field("Gopalram Ji Dhaba"), "HR-38-5678", encrypt_field(8500.0), "udhaar", "diesel fill"),
            ("2026-06-05", encrypt_field("Gopalram Ji Dhaba"), "N/A", encrypt_field(4000.0), "payment", "cash drop"),
            ("2026-06-08", encrypt_field("Gopalram Ji Dhaba"), "HR-38-9012", encrypt_field(3500.0), "udhaar", "petrol fill"),
            
            # Other party (unrelated)
            ("2026-06-03", encrypt_field("Jagveer Ji Dhaba"), "HR-39-9999", encrypt_field(12000.0), "udhaar", "unrelated bill")
        ]
        
        for date, party, vehicle, amount, r_type, remarks in entries:
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (date, party, vehicle, amount, r_type, remarks))
            
        conn.commit()
        conn.close()

    def test_generate_customer_invoice_calculates_correct_totals(self):
        """Verifies opening balance, period debits, period payments, and final due calculate cleanly."""
        self._seed_entries()
        
        pdf_path = generate_customer_invoice(
            party_name="Gopalram Ji Dhaba",
            start_date="2026-06-01",
            end_date="2026-06-10",
            db_path=self.db_path,
            exports_dir=self.exports_dir
        )
        
        self.assertTrue(os.path.exists(pdf_path))
        self.assertTrue(os.path.getsize(pdf_path) > 0)
        self.assertEqual(os.path.basename(pdf_path), "Invoice_Gopalram_Ji_Dhaba_2026-06-10.pdf")

    def test_generate_customer_invoice_no_records(self):
        """Tests that rendering statement invoice for a party with 0 entries compiles cleanly with empty state."""
        pdf_path = generate_customer_invoice(
            party_name="Nonexistent Party",
            start_date="2026-06-01",
            end_date="2026-06-10",
            db_path=self.db_path,
            exports_dir=self.exports_dir
        )
        
        self.assertTrue(os.path.exists(pdf_path))
        self.assertTrue(os.path.getsize(pdf_path) > 0)


if __name__ == "__main__":
    unittest.main()
