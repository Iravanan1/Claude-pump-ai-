#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Fuel Purchase Cost Price Registry.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
import shutil

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import purchase_registry
import migrations

class TestPurchaseRegistry(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary sandbox database for clean isolation
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        
        # Apply migrations up to Version 7 (creating the purchase_cost_log table)
        migrations.apply_schema_updates(self.temp_db_path)

    def tearDown(self):
        # Close file descriptor and remove temporary file cleanly
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_migration_version_7_schema(self):
        """Verify that the purchase_cost_log table exists and has correct columns/constraints."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        # Assert database schema version is at least 7
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        version = cursor.fetchone()[0]
        self.assertGreaterEqual(version, 7)
        
        # Check purchase_cost_log columns
        cursor.execute("PRAGMA table_info(purchase_cost_log)")
        cols = {c[1]: (c[2], c[3], c[5]) for c in cursor.fetchall()} # name -> (type, notnull, pk)
        
        self.assertIn("effective_date", cols)
        self.assertIn("product_type", cols)
        self.assertIn("purchase_rate_per_liter", cols)
        self.assertIn("invoice_reference", cols)
        
        # Type and constraint checks
        self.assertEqual(cols["effective_date"][2], 1) # Part of PK
        self.assertEqual(cols["product_type"][2], 2)   # Part of PK (2nd column in composite PK)
        self.assertEqual(cols["purchase_rate_per_liter"][1], 1) # NOT NULL
        
        conn.close()

    def test_upsert_and_get_all_purchase_rates(self):
        """Verify adding, updating, and listing purchase rates."""
        # Check empty state
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 0)
        
        # Add a rate
        purchase_registry.upsert_purchase_rate(
            effective_date="2026-06-01",
            product_type="HSD",
            purchase_rate_per_liter=88.50,
            invoice_reference="INV-001",
            db_path=self.temp_db_path
        )
        
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["effective_date"], "2026-06-01")
        self.assertEqual(rates[0]["product_type"], "HSD")
        self.assertEqual(rates[0]["purchase_rate_per_liter"], 88.50)
        self.assertEqual(rates[0]["invoice_reference"], "INV-001")
        
        # Update/Overwrite the same rate (matching date and product)
        purchase_registry.upsert_purchase_rate(
            effective_date="2026-06-01",
            product_type="HSD",
            purchase_rate_per_liter=89.00,
            invoice_reference="INV-001-REV",
            db_path=self.temp_db_path
        )
        
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["purchase_rate_per_liter"], 89.00)
        self.assertEqual(rates[0]["invoice_reference"], "INV-001-REV")
        
        # Add another product rate on same date
        purchase_registry.upsert_purchase_rate(
            effective_date="2026-06-01",
            product_type="MS",
            purchase_rate_per_liter=98.75,
            invoice_reference="INV-002",
            db_path=self.temp_db_path
        )
        
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 2)

    def test_delete_purchase_rate(self):
        """Verify deleting a purchase rate."""
        purchase_registry.upsert_purchase_rate("2026-06-01", "HSD", 88.50, "INV-1", self.temp_db_path)
        purchase_registry.upsert_purchase_rate("2026-06-01", "MS", 98.50, "INV-2", self.temp_db_path)
        
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 2)
        
        # Delete HSD rate
        purchase_registry.delete_purchase_rate("2026-06-01", "HSD", self.temp_db_path)
        
        rates = purchase_registry.get_all_purchase_rates(self.temp_db_path)
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0]["product_type"], "MS")

    def test_get_effective_purchase_cost_lookup(self):
        """Verify the chronological lookup and fallback logic."""
        # 1. Test empty table returns None
        cost = purchase_registry.get_effective_purchase_cost("2026-06-05", "HSD", self.temp_db_path)
        self.assertIsNone(cost)
        
        # 2. Seed some cost rates
        # June 1: HSD = 80.00
        purchase_registry.upsert_purchase_rate("2026-06-01", "HSD", 80.00, "INV-A", self.temp_db_path)
        # June 4: HSD = 82.50
        purchase_registry.upsert_purchase_rate("2026-06-04", "HSD", 82.50, "INV-B", self.temp_db_path)
        # June 10: HSD = 85.00
        purchase_registry.upsert_purchase_rate("2026-06-10", "HSD", 85.00, "INV-C", self.temp_db_path)
        
        # June 1: MS = 90.00
        purchase_registry.upsert_purchase_rate("2026-06-01", "MS", 90.00, "INV-D", self.temp_db_path)

        # 3. Test exact match on June 4 (HSD)
        cost_exact = purchase_registry.get_effective_purchase_cost("2026-06-04", "HSD", self.temp_db_path)
        self.assertEqual(cost_exact, 82.50)
        
        # 4. Test exact match on June 1 (HSD)
        cost_exact_1 = purchase_registry.get_effective_purchase_cost("2026-06-01", "HSD", self.temp_db_path)
        self.assertEqual(cost_exact_1, 80.00)
        
        # 5. Test fallback match on June 3 (should return June 1 rate: 80.00)
        cost_fallback_3 = purchase_registry.get_effective_purchase_cost("2026-06-03", "HSD", self.temp_db_path)
        self.assertEqual(cost_fallback_3, 80.00)
        
        # 6. Test fallback match on June 5 (should return June 4 rate: 82.50)
        cost_fallback_5 = purchase_registry.get_effective_purchase_cost("2026-06-05", "HSD", self.temp_db_path)
        self.assertEqual(cost_fallback_5, 82.50)
        
        # 7. Test fallback match on June 20 (should return June 10 rate: 85.00)
        cost_fallback_20 = purchase_registry.get_effective_purchase_cost("2026-06-20", "HSD", self.temp_db_path)
        self.assertEqual(cost_fallback_20, 85.00)
        
        # 8. Test lookup before first entry (June 1) -> should return None
        cost_before = purchase_registry.get_effective_purchase_cost("2026-05-31", "HSD", self.temp_db_path)
        self.assertIsNone(cost_before)

        # 9. Test other product lookup (MS)
        cost_ms = purchase_registry.get_effective_purchase_cost("2026-06-05", "MS", self.temp_db_path)
        self.assertEqual(cost_ms, 90.00)

    def test_invalid_arguments_handling(self):
        """Verify errors are raised/handled correctly for bad inputs."""
        with self.assertRaises(ValueError):
            purchase_registry.upsert_purchase_rate("2026-06-01", "INVALID", 80.00, db_path=self.temp_db_path)
            
        with self.assertRaises(ValueError):
            purchase_registry.upsert_purchase_rate("2026-06-01", "HSD", -1.0, db_path=self.temp_db_path)
            
        # Lookup on invalid product should return None
        self.assertIsNone(purchase_registry.get_effective_purchase_cost("2026-06-01", "INVALID", self.temp_db_path))

if __name__ == "__main__":
    unittest.main()
