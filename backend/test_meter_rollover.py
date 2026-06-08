#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Nozzle Flow Meter Rollover and Mechanical Reset Calculations.
Asserts standard subtraction, mechanical rollovers, manual overrides, and SQLite schema verification.
"""

import os
import unittest
import sqlite3
import tempfile

from meter_rollover import calculate_net_nozzle_volume
import migrations

class TestMeterRollover(unittest.TestCase):
    def test_standard_subtraction(self):
        """If closing >= opening, net flow is closing - opening."""
        self.assertEqual(calculate_net_nozzle_volume(100.0, 250.0), 150.0)
        self.assertEqual(calculate_net_nozzle_volume(0.0, 0.0), 0.0)
        self.assertEqual(calculate_net_nozzle_volume(50000.45, 50020.95), 20.5)

    def test_mechanical_rollover(self):
        """If closing < opening, detect rollover and use max_digits ceiling."""
        # (999999 - 999990) + 10 = 9 + 10 = 19
        self.assertEqual(calculate_net_nozzle_volume(999990.0, 10.0, max_digits=999999), 19.0)
        # Custom max digits: (999 - 990) + 5 = 9 + 5 = 14
        self.assertEqual(calculate_net_nozzle_volume(990.0, 5.0, max_digits=999), 14.0)

    def test_manual_meter_replacement_override(self):
        """If meter_replaced is True, net flow is (closing - opening) + replacement_offset_liters."""
        # Replaced: (50.0 - 10.0) + 120.0 = 160.0
        self.assertEqual(calculate_net_nozzle_volume(10.0, 50.0, meter_replaced=True, replacement_offset_liters=120.0), 160.0)
        # If offset is 0
        self.assertEqual(calculate_net_nozzle_volume(10.0, 50.0, meter_replaced=True, replacement_offset_liters=0.0), 40.0)

    def test_schema_verification_current_version(self):
        """Verify that running migrations upgrades the database to the current
        maximum version (6) and adds target columns to daily_summary and ledger_entries."""
        temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        try:
            # Apply migrations
            migrations.apply_schema_updates(temp_db_path)
            
            # Connect and verify
            conn = sqlite3.connect(temp_db_path)
            cursor = conn.cursor()
            
            # Check version is at maximum (v6 = accounting_head migration)
            cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertGreaterEqual(row[0], 6)
            
            # Check columns added by v5 migration in daily_summary
            cursor.execute("PRAGMA table_info(daily_summary)")
            cols = {c[1]: c[2] for c in cursor.fetchall()}
            self.assertIn("meter_replaced", cols)
            self.assertIn("replacement_offset_liters", cols)

            # Check column added by v6 migration in ledger_entries
            cursor.execute("PRAGMA table_info(ledger_entries)")
            ledger_cols = {c[1] for c in cursor.fetchall()}
            self.assertIn("accounting_head", ledger_cols)
            
            conn.close()
        finally:
            os.close(temp_db_fd)
            try:
                os.remove(temp_db_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
