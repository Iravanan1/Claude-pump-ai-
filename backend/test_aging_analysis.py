#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Credit Ledger Aging Analysis & Risk Evaluation Engine.
"""

import os
import sys
import sqlite3
import unittest
import tempfile
import shutil
import datetime
from fastapi.testclient import TestClient

# Ensure backend directory is on path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from crypto_vault import encrypt_field
import main
import aging_analysis
import fifo_settler


class TestAgingAnalysis(unittest.TestCase):
    """Isolated test harness for Credit Aging Analysis and Risk Engine."""
    
    def setUp(self):
        """Create fresh test database, run migrations, and seed encrypted credit records."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ledger.db")
        
        # Patch active DB_PATHs in the modules
        self.original_main_db = main.DB_PATH
        self.original_aging_db = aging_analysis.DB_PATH
        self.original_fifo_db = fifo_settler.DB_PATH
        
        main.DB_PATH = self.db_path
        aging_analysis.DB_PATH = self.db_path
        fifo_settler.DB_PATH = self.db_path
        
        # Setup tables
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create base ledger_entries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                party_name TEXT,
                vehicle_wheel_no TEXT,
                amount REAL,
                type TEXT,
                remarks TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                transaction_source TEXT DEFAULT 'manual'
            )
        """)
        
        # Run FIFO columns migration
        fifo_settler.ensure_fifo_columns(self.db_path)
        
        # Define a consistent reference date for testing: 2026-06-01
        self.ref_date = "2026-06-01"
        
        # Seed credit entries for "Sharma Transports" with different aging days
        # Boundary 1: 5 days old (Current: 0-15d) -> 1000.0
        # Boundary 2: 20 days old (Growing: 16-30d) -> 2000.0
        # Boundary 3: 40 days old (Delinquent: 31-60d) -> 3000.0
        # Boundary 4: 75 days old (Critical: >60d) -> 4000.0
        seed_entries = [
            ("2026-05-27", 1000.0, "udhaar", "Current HSD sale"),      # 5 days old
            ("2026-05-12", 2000.0, "udhaar", "Growing HSD sale"),      # 20 days old
            ("2026-04-22", 3000.0, "udhaar", "Delinquent HSD sale"),   # 40 days old
            ("2026-03-18", 4000.0, "udhaar", "Critical HSD sale"),     # 75 days old
        ]
        
        for dt_str, amount, etype, remarks in seed_entries:
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
                VALUES (?, ?, ?, ?, ?, ?, 'UNPAID', ?)
            """, (
                dt_str,
                encrypt_field("Sharma Transports"),
                "RJ14GA1234",
                encrypt_field(amount),
                etype,
                remarks,
                encrypt_field(amount)
            ))
            
        # Seed another customer "Gopalram Ji" with healthy current outstanding only (10 days old -> 5000.0)
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
            VALUES (?, ?, ?, ?, ?, ?, 'UNPAID', ?)
        """, (
            "2026-05-22",
            encrypt_field("Gopalram Ji"),
            "RJ14GA5555",
            encrypt_field(5000.0),
            "udhaar",
            "Current MS sale",
            encrypt_field(5000.0)
        ))
        
        conn.commit()
        conn.close()
        
        self.client = TestClient(main.app)
        
    def tearDown(self):
        """Restore original DB_PATH settings and clean up temp folder."""
        main.DB_PATH = self.original_main_db
        aging_analysis.DB_PATH = self.original_aging_db
        fifo_settler.DB_PATH = self.original_fifo_db
        shutil.rmtree(self.test_dir, ignore_errors=True)
        
    def test_compile_customer_debt_aging_boundary_logic(self):
        """Verify that outstanding credit correctly resolves to precise age windows and risk indices."""
        res = aging_analysis.compile_customer_debt_aging(
            party_name="Sharma Transports",
            reference_date=self.ref_date,
            db_path=self.db_path
        )
        
        self.assertEqual(res["party_name"], "Sharma Transports")
        self.assertEqual(res["total_outstanding"], 10000.0)
        
        # Verify chronological buckets
        self.assertEqual(res["buckets"]["current"], 1000.0)
        self.assertEqual(res["buckets"]["growing"], 2000.0)
        self.assertEqual(res["buckets"]["delinquent"], 3000.0)
        self.assertEqual(res["buckets"]["critical"], 4000.0)
        
        # Verify Risk Coefficient:
        # Weighted sum: (0.0 * 1000) + (0.15 * 2000) + (0.50 * 3000) + (1.00 * 4000) = 300 + 1500 + 4000 = 5800
        # Percentage coefficient: 5800 / 10000 * 100 = 58.0%
        self.assertEqual(res["risk_coefficient_pct"], 58.0)
        
        # Must flag Overdue collection alert because critical > 0
        self.assertTrue(res["collection_required_alert"])

    def test_compile_healthy_customer_aging(self):
        """Verify that a customer with only recent debt maintains a lower risk and no alerts."""
        res = aging_analysis.compile_customer_debt_aging(
            party_name="Gopalram Ji",
            reference_date=self.ref_date,
            db_path=self.db_path
        )
        self.assertEqual(res["total_outstanding"], 5000.0)
        self.assertEqual(res["buckets"]["current"], 5000.0)
        self.assertEqual(res["buckets"]["growing"], 0.0)
        self.assertEqual(res["buckets"]["delinquent"], 0.0)
        self.assertEqual(res["buckets"]["critical"], 0.0)
        
        # Risk Coefficient must be 0% for pure Current debt (weight 0.0)
        self.assertEqual(res["risk_coefficient_pct"], 0.0)
        self.assertFalse(res["collection_required_alert"])

    def test_compile_all_customers_aging(self):
        """Verify summary aggregates all unique credit parties and sorts descending by debt."""
        summary = aging_analysis.compile_all_customers_aging(
            reference_date=self.ref_date,
            db_path=self.db_path
        )
        
        self.assertEqual(len(summary), 2)
        # Should be sorted with largest outstanding first: Sharma Transports (10k) then Gopalram Ji (5k)
        self.assertEqual(summary[0]["party_name"], "Sharma Transports")
        self.assertEqual(summary[1]["party_name"], "Gopalram Ji")

    def test_export_aging_summary_pdf(self):
        """Verify Landscape A4 PDF gets created successfully using fitz and is readable."""
        out_pdf = os.path.join(self.test_dir, "credit_aging_statement.pdf")
        aging_analysis.export_aging_summary_pdf(
            output_path=out_pdf,
            reference_date=self.ref_date,
            db_path=self.db_path
        )
        
        self.assertTrue(os.path.exists(out_pdf))
        self.assertGreater(os.path.getsize(out_pdf), 0)
        
        # Basic PyMuPDF integrity validation
        import fitz
        doc = fitz.open(out_pdf)
        self.assertGreater(len(doc), 0)
        # Verify page width and landscape dimension (842 x 595)
        page = doc[0]
        self.assertAlmostEqual(page.rect.width, 842, delta=1)
        self.assertAlmostEqual(page.rect.height, 595, delta=1)
        doc.close()

    def test_api_aging_endpoints(self):
        """Verify that all FastAPI endpoints handle requests and yield correct payload types."""
        # 1. Test GET /api/aging/customer
        response = self.client.get(f"/api/aging/customer?party_name=Sharma%20Transports&reference_date={self.ref_date}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["party_name"], "Sharma Transports")
        self.assertEqual(data["total_outstanding"], 10000.0)
        self.assertEqual(data["risk_coefficient_pct"], 58.0)
        self.assertTrue(data["collection_required_alert"])
        
        # 2. Test GET /api/aging/summary
        response = self.client.get(f"/api/aging/summary?reference_date={self.ref_date}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["summary"]), 2)
        
        # 3. Test GET /api/aging/export-pdf
        response = self.client.get(f"/api/aging/export-pdf?reference_date={self.ref_date}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(len(response.content) > 0)


if __name__ == "__main__":
    unittest.main()
