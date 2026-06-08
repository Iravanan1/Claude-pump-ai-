#!/usr/bin/env python3
"""
Unit and Integration Test Suite for the Local Hardware Thermal Printer driver.
Asserts column alignment spacing, transaction mapping formats, and dry-run disk file caching.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
import subprocess

import thermal_printer

class TestThermalPrinter(unittest.TestCase):
    def setUp(self):
        self.party_name = "Gopalram"
        self.transactions = [
            {"date": "2026-05-28", "vehicle_no": "RJ-14-GH-2931", "amount": 4500.0},
            {"date": "2026-05-30", "vehicle_no": "MH-02-AB-1234", "amount": 8500.0}
        ]
        self.net_due = 13000.0

    def test_format_48col_ascii_exact_width_and_headers(self):
        """Verifies that the generated ASCII slip pads columns correctly to exactly 48 characters width."""
        ascii_slip = thermal_printer.format_48col_ascii(self.party_name, self.transactions, self.net_due)
        lines = [line for line in ascii_slip.splitlines() if line]
        
        # Assert each grid divider and structural row is exactly 48 characters wide
        for line in lines:
            if "TOTAL OUTSTANDING" in line:
                self.assertEqual(len(line), 48)
            elif "Date" in line and "Vehicle No" in line:
                self.assertEqual(len(line), 48)
            elif "MH-02-AB-1234" in line:
                self.assertEqual(len(line), 48)
                
        # Assert content is rendered cleanly
        self.assertIn("PUMPAI FUEL STATION", ascii_slip)
        self.assertIn("Customer Name: Gopalram", ascii_slip)
        self.assertIn("RJ-14-GH-2931", ascii_slip)
        self.assertIn("13,000.00", ascii_slip)

    def test_print_credit_ledger_slip_dry_run_saves_file(self):
        """Verifies that running print_credit_ledger_slip in dry-run mode writes a clean receipt to disk."""
        result = thermal_printer.print_credit_ledger_slip(
            self.party_name,
            self.transactions,
            self.net_due,
            dry_run=True
        )
        
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["dry_run"])
        self.assertIsNotNone(result["receipt_path"])
        
        # Verify file exists and is populated
        receipt_path = result["receipt_path"]
        self.assertTrue(os.path.exists(receipt_path))
        
        with open(receipt_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("PUMPAI FUEL STATION", content)
            self.assertIn("Customer Name: Gopalram", content)
            self.assertIn("TOTAL OUTSTANDING:", content)
            
        # Clean up temporary dry-run output file cleanly
        try:
            os.remove(receipt_path)
        except OSError:
            pass

    @patch("thermal_printer.subprocess.check_output")
    def test_connect_thermal_printer_auto_discovery(self, mock_check_output):
        """Verifies auto-discovery identifies thermal queue printers or falls back gracefully."""
        # 1. Mock lpstat -p output with a connected epson thermal printer
        mock_check_output.return_value = (
            b"printer EPSON_TM_T82 is idle. enabled since Sun May 31 16:00:00 2026\n"
            b"printer Star_Receipt is idle. enabled since Sun May 31 16:00:00 2026\n"
        )
        
        # Patch exists utility check to return true for /usr/bin/lpstat
        with patch("thermal_printer.os.path.exists", return_value=True):
            status = thermal_printer.connect_thermal_printer()
            self.assertTrue(status["connected"])
            self.assertEqual(status["printer_name"], "EPSON_TM_T82")
            self.assertEqual(status["method"], "lp")
            self.assertIn("Connected successfully", status["message"])

    @patch("thermal_printer.subprocess.check_output")
    def test_connect_thermal_printer_fallback_default(self, mock_check_output):
        """Verifies that if no epson/thermal named printer exists, it falls back to the first available printer."""
        mock_check_output.return_value = (
            b"printer Office_Laserjet is idle. enabled since Sun May 31 16:00:00 2026\n"
        )
        
        with patch("thermal_printer.os.path.exists", return_value=True):
            status = thermal_printer.connect_thermal_printer()
            self.assertTrue(status["connected"])
            self.assertEqual(status["printer_name"], "Office_Laserjet")

if __name__ == "__main__":
    unittest.main()
