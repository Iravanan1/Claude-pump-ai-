#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Staff Cash Advance & Salary Deduction Registry.
"""

import os
import sys
import sqlite3
import unittest
import tempfile
import shutil
from fastapi.testclient import TestClient

# Ensure backend directory is on path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import main
import staff_ledger
from ai_engine import run_claude_accounting_guardrails, run_gemini_vision_extraction


class TestStaffLedger(unittest.TestCase):
    """Isolated test harness for Staff Cash Advances and Salary Deduction Registry."""
    
    def setUp(self):
        """Create fresh test database, run migrations, and seed employee advances."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ledger.db")
        
        # Patch active DB_PATHs in the modules
        self.original_main_db = main.DB_PATH
        self.original_staff_db = staff_ledger.DB_PATH
        
        main.DB_PATH = self.db_path
        staff_ledger.DB_PATH = self.db_path
        
        # Initialize staff advances tables
        staff_ledger.init_staff_ledger_db(self.db_path)
        
        # Seed test data for Ramesh across multiple months
        # May 2026 drawings
        staff_ledger.record_staff_advance(
            date="2026-05-10",
            employee_name="Ramesh",
            amount_drawn=500.0,
            atype="CASH_ADVANCE",
            remarks="DSM advance 500",
            settlement_status="PENDING_DEDUCTION",
            db_path=self.db_path
        )
        staff_ledger.record_staff_advance(
            date="2026-05-15",
            employee_name="Ramesh",
            amount_drawn=200.0,
            atype="FUEL_DRAWN",
            remarks="diesel 200",
            settlement_status="PENDING_DEDUCTION",
            db_path=self.db_path
        )
        # June 2026 drawing (should be isolated from May query)
        staff_ledger.record_staff_advance(
            date="2026-06-01",
            employee_name="Ramesh",
            amount_drawn=400.0,
            atype="CASH_ADVANCE",
            remarks="DSM advance 400",
            settlement_status="PENDING_DEDUCTION",
            db_path=self.db_path
        )
        # Settle one drawing to verify payroll query excludes SETTLED drawings
        settled_id = staff_ledger.record_staff_advance(
            date="2026-05-20",
            employee_name="Ramesh",
            amount_drawn=1000.0,
            atype="CASH_ADVANCE",
            remarks="settled drawing",
            settlement_status="PENDING_DEDUCTION",
            db_path=self.db_path
        )
        staff_ledger.settle_staff_advance(settled_id, "SETTLED_FROM_SALARY", self.db_path)
        
        # Seed test data for Suresh
        staff_ledger.record_staff_advance(
            date="2026-05-12",
            employee_name="Suresh",
            amount_drawn=300.0,
            atype="CASH_ADVANCE",
            remarks="Suresh advance",
            settlement_status="PENDING_DEDUCTION",
            db_path=self.db_path
        )
        
        self.client = TestClient(main.app)
        
    def tearDown(self):
        """Restore original DB_PATH settings and clean up temp folder."""
        main.DB_PATH = self.original_main_db
        staff_ledger.DB_PATH = self.original_staff_db
        shutil.rmtree(self.test_dir, ignore_errors=True)
        
    def test_record_staff_advance_validation(self):
        """Verify type constraints and amount checks."""
        # Invalid advance type
        with self.assertRaises(ValueError):
            staff_ledger.record_staff_advance(
                date="2026-05-25",
                employee_name="Mahesh",
                amount_drawn=500.0,
                atype="PERSONAL_LOAN",
                remarks="invalid type",
                db_path=self.db_path
            )
            
        # Zero or negative amount
        with self.assertRaises(ValueError):
            staff_ledger.record_staff_advance(
                date="2026-05-25",
                employee_name="Mahesh",
                amount_drawn=-100.0,
                atype="CASH_ADVANCE",
                remarks="negative amount",
                db_path=self.db_path
            )

    def test_get_daily_staff_advances(self):
        """Verify retrieving staff advances on a specific date."""
        advances = staff_ledger.get_daily_staff_advances("2026-05-10", self.db_path)
        self.assertEqual(len(advances), 1)
        self.assertEqual(advances[0]["employee_name"], "Ramesh")
        self.assertEqual(advances[0]["amount_drawn"], 500.0)

    def test_generate_monthly_payroll_deductions_aggregation(self):
        """Verify target month isolation and sum arithmetic."""
        # Query Ramesh for May 2026
        # May drawings: 500.0 (pending), 200.0 (pending). The 1000.0 is settled. June is 400.0.
        receipt = staff_ledger.generate_monthly_payroll_deductions(
            employee_name="Ramesh",
            target_month="2026-05",
            db_path=self.db_path
        )
        
        self.assertEqual(receipt["employee_name"], "Ramesh")
        self.assertEqual(receipt["target_month"], "2026-05")
        self.assertEqual(receipt["total_deduction_amount"], 700.0)
        self.assertEqual(receipt["deductions_count"], 2)
        
        # Verify specific items
        adv_dates = {a["date"] for a in receipt["advances"]}
        self.assertIn("2026-05-10", adv_dates)
        self.assertIn("2026-05-15", adv_dates)
        self.assertNotIn("2026-05-20", adv_dates)  # settled
        self.assertNotIn("2026-06-01", adv_dates)  # wrong month

    def test_generate_monthly_payroll_deductions_invalid_month(self):
        """Verify payroll query checks target month format constraints."""
        with self.assertRaises(ValueError):
            staff_ledger.generate_monthly_payroll_deductions("Ramesh", "May 2026", self.db_path)

    def test_api_staff_advances_endpoints(self):
        """Verify API recording, querying, and payroll generation routes."""
        # 1. Test POST /api/staff-advances
        resp = self.client.post("/api/staff-advances", json={
            "date": "2026-05-28",
            "employee_name": "Mahesh",
            "amount_drawn": 600.0,
            "type": "FUEL_DRAWN",
            "remarks": "diesel draw Mahesh"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        adv_id = resp.json()["advance_id"]
        
        # Verify in DB
        advances = staff_ledger.get_daily_staff_advances("2026-05-28", self.db_path)
        self.assertEqual(len(advances), 1)
        self.assertEqual(advances[0]["employee_name"], "Mahesh")
        
        # 2. Test GET /api/staff-advances
        resp = self.client.get("/api/staff-advances?date=2026-05-28")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["advances"]), 1)
        
        # 3. Test POST /api/staff-advances/settle
        resp = self.client.post("/api/staff-advances/settle", json={
            "advance_id": adv_id,
            "settlement_status": "SETTLED_FROM_SALARY"
        })
        self.assertEqual(resp.status_code, 200)
        
        # Verify settled
        advances = staff_ledger.get_daily_staff_advances("2026-05-28", self.db_path)
        self.assertEqual(advances[0]["settlement_status"], "SETTLED_FROM_SALARY")
        
        # 4. Test GET /api/staff-advances/payroll
        resp = self.client.get("/api/staff-advances/payroll?employee_name=Ramesh&target_month=2026-05")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_deduction_amount"], 700.0)


if __name__ == "__main__":
    unittest.main()
