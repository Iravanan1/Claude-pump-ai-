#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Sales Target Analytics Engine.
"""

import os
import sys
import unittest
import sqlite3
import tempfile
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

import init_db
import migrations
import main
import price_registry

class TestSalesTargetsAnalytics(unittest.TestCase):
    
    def setUp(self):
        # Redirect DB paths for isolation
        self.original_init_db = init_db.DB_PATH
        self.original_main_db = main.DB_PATH
        self.original_price_db = price_registry.DB_PATH
        
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        
        init_db.DB_PATH = self.temp_db_path
        main.DB_PATH = self.temp_db_path
        price_registry.DB_PATH = self.temp_db_path
        
        # Initialize the database (which runs up to migration 9 now)
        init_db.initialize_database()
        
        # Initialize TestClient
        self.client = TestClient(main.app)
        
    def tearDown(self):
        # Restore DB paths
        init_db.DB_PATH = self.original_init_db
        main.DB_PATH = self.original_main_db
        price_registry.DB_PATH = self.original_price_db
        
        # Clean up temporary database
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_migration_version_9_schema(self):
        """Verify that migration VERSION 9 successfully creates and seeds fixed_overhead_config."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        # Check database schema version is at least 9
        cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
        version = cursor.fetchone()[0]
        self.assertGreaterEqual(version, 9)
        
        # Verify table exists and has columns
        cursor.execute("PRAGMA table_info(fixed_overhead_config)")
        cols = {c[1] for c in cursor.fetchall()}
        self.assertIn("key", cols)
        self.assertIn("value", cols)
        
        # Verify seeded monthly overhead
        cursor.execute("SELECT value FROM fixed_overhead_config WHERE key = 'monthly_overhead'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1500000.0)
        
        conn.close()

    def test_get_sales_targets_empty_db(self):
        """Test sales targets endpoint calculations with an empty database state."""
        response = self.client.get("/api/analytics/sales-targets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["monthly_overhead"], 1500000.0)
        self.assertEqual(data["average_margin_per_liter"], 4.0) # default fallback
        
        # Target daily sales volume math: (1,500,000 / 30) / 4.0 = 50,000 / 4 = 12,500 Liters
        self.assertAlmostEqual(data["target_daily_sales_volume"], 12500.0)
        self.assertEqual(data["current_daily_volume"], 0.0)
        self.assertEqual(data["progress_percentage"], 0.0)
        self.assertAlmostEqual(data["volume_gap"], 12500.0)
        self.assertIn("Volume Gap: You need to sell 12,500 more Liters of HSD today", data["micro_label"])
        self.assertEqual(len(data["monthly_history"]), 0)

    def test_get_sales_targets_calculations(self):
        """Verify that target Daily volume, progress circles, and gaps compute accurately with active daily logs."""
        conn = sqlite3.connect(self.temp_db_path)
        cursor = conn.cursor()
        
        # 1. Seed trailing 30 recorded daily summaries (we seed 2 days for simplicity)
        # Day 1: 1000L sold, spread_usd = 60.24 (INR equivalent: 60.24 * 83.0 = 4,999.92 INR)
        # Day 2: 1500L sold, spread_usd = 90.36 (INR equivalent: 90.36 * 83.0 = 7,499.88 INR)
        # Total liters = 2500.0
        # Total spread = 12499.80 INR
        # Avg margin per liter = 12499.8 / 2500 = 5.0 INR/L
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (date, total_hsd_liters, total_ms_liters, gross_spread_usd, realized_profit, is_verified)
            VALUES (?, ?, ?, ?, ?, 1)
        """, ("2026-06-01", 400.0, 600.0, 5000.0 / 83.0, 4500.0))
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_summary (date, total_hsd_liters, total_ms_liters, gross_spread_usd, realized_profit, is_verified)
            VALUES (?, ?, ?, ?, ?, 1)
        """, ("2026-06-02", 700.0, 800.0, 7500.0 / 83.0, 7000.0))
        
        conn.commit()
        conn.close()
        
        # Fetch targets
        response = self.client.get("/api/analytics/sales-targets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["monthly_overhead"], 1500000.0)
        self.assertAlmostEqual(data["average_margin_per_liter"], 5.0, places=2)
        
        # Target daily volume math: (1,500,000 / 30) / 5.0 = 50,000 / 5 = 10,000 Liters
        self.assertAlmostEqual(data["target_daily_sales_volume"], 10000.0)
        
        # Current daily volume (latest day is 2026-06-02): 700 + 800 = 1500 Liters
        self.assertEqual(data["current_daily_volume"], 1500.0)
        
        # Progress: 1500 / 10000 = 15%
        self.assertEqual(data["progress_percentage"], 15.0)
        
        # Gap: 10000 - 1500 = 8500 Liters
        self.assertEqual(data["volume_gap"], 8500.0)
        self.assertIn("Volume Gap: You need to sell 8,500 more Liters of HSD today", data["micro_label"])
        
        # Monthly history (grouped by June 2026): 4500 + 7000 = 11500
        self.assertEqual(len(data["monthly_history"]), 1)
        self.assertEqual(data["monthly_history"][0]["month"], "2026-06")
        self.assertEqual(data["monthly_history"][0]["monthly_profit"], 11500.0)

    def test_save_overhead_configuration(self):
        """Verify updating overhead config via POST endpoint."""
        # Test bad inputs
        response = self.client.post("/api/analytics/sales-targets/config", json={"monthly_overhead": -100})
        self.assertEqual(response.status_code, 400)
        
        # Test valid input
        response = self.client.post("/api/analytics/sales-targets/config", json={"monthly_overhead": 1200000.0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        
        # Verify db was updated and recalculates with new overhead
        response = self.client.get("/api/analytics/sales-targets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["monthly_overhead"], 1200000.0)
        # Average margin is 4.0 (empty db fallback).
        # Target daily volume math: (1,200,000 / 30) / 4.0 = 40,000 / 4 = 10,000 Liters
        self.assertAlmostEqual(data["target_daily_sales_volume"], 10000.0)

if __name__ == "__main__":
    unittest.main()
