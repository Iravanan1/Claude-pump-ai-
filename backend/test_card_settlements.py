"""
Unit test suite for card_settlements.py
"""

import os
import unittest
import sqlite3
import tempfile
import shutil

import card_settlements

class TestCardSettlements(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary directory and database path
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_ledger.db")
        
        # Initialize database schema
        card_settlements.init_card_settlements_db(self.test_db)

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_init_db_creates_table_and_index(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='card_settlements'")
        self.assertIsNotNone(cursor.fetchone())
        
        # Verify index exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_card_settlements_date'")
        self.assertIsNotNone(cursor.fetchone())
        
        conn.close()

    def test_calculate_net_settlement_rates(self):
        # 1. RuPay / Debit Card (0% MDR)
        charges, net = card_settlements.calculate_net_settlement(10000.0, "RuPay POS")
        self.assertEqual(charges, 0.0)
        self.assertEqual(net, 10000.0)

        charges, net = card_settlements.calculate_net_settlement(5000.0, "SBI Debit Card")
        self.assertEqual(charges, 0.0)
        self.assertEqual(net, 5000.0)

        # 2. HDFC POS / Commercial (0.9% MDR)
        charges, net = card_settlements.calculate_net_settlement(10000.0, "HDFC POS")
        self.assertEqual(charges, 90.0)
        self.assertEqual(net, 9910.0)

        charges, net = card_settlements.calculate_net_settlement(10000.0, "Commercial Card")
        self.assertEqual(charges, 90.0)
        self.assertEqual(net, 9910.0)

        # 3. SBI Touch / standard credit card POS (0.75% MDR)
        charges, net = card_settlements.calculate_net_settlement(10000.0, "SBI Touch")
        self.assertEqual(charges, 75.0)
        self.assertEqual(net, 9925.0)

        # 4. Default / Generic Fallback Card (1.0% MDR)
        charges, net = card_settlements.calculate_net_settlement(10000.0, "Generic Card")
        self.assertEqual(charges, 100.0)
        self.assertEqual(net, 9900.0)

        # 5. Zero / None amount handling
        charges, net = card_settlements.calculate_net_settlement(0.0, "HDFC POS")
        self.assertEqual(charges, 0.0)
        self.assertEqual(net, 0.0)

    def test_save_and_retrieve_card_settlements(self):
        settlements = [
            {"machine_provider": "HDFC POS", "gross_swipes_copied": 12400.0},
            {"machine_provider": "SBI Touch", "gross_swipes_copied": 8500.0},
            {"machine_provider": "RuPay Debit", "gross_swipes_copied": 5000.0}
        ]
        
        # Save settlements
        card_settlements.save_card_settlements("2026-06-01", settlements, db_path=self.test_db)
        
        # Retrieve and verify
        retrieved = card_settlements.get_card_settlements_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(retrieved), 3)
        
        # Verify first settlement (HDFC POS - 0.9% MDR expected)
        self.assertEqual(retrieved[0]["machine_provider"], "HDFC POS")
        self.assertEqual(retrieved[0]["gross_swipes_copied"], 12400.0)
        self.assertEqual(retrieved[0]["bank_charges_mdr"], 111.60) # 12400 * 0.009
        self.assertEqual(retrieved[0]["expected_net_credit"], 12288.40)
        self.assertEqual(retrieved[0]["reconciliation_status"], "Pending")

        # Verify second settlement (SBI Touch - 0.75% MDR expected)
        self.assertEqual(retrieved[1]["machine_provider"], "SBI Touch")
        self.assertEqual(retrieved[1]["gross_swipes_copied"], 8500.0)
        self.assertEqual(retrieved[1]["bank_charges_mdr"], 63.75) # 8500 * 0.0075
        self.assertEqual(retrieved[1]["expected_net_credit"], 8436.25)

        # Verify third settlement (RuPay Debit - 0% MDR expected)
        self.assertEqual(retrieved[2]["machine_provider"], "RuPay Debit")
        self.assertEqual(retrieved[2]["gross_swipes_copied"], 5000.0)
        self.assertEqual(retrieved[2]["bank_charges_mdr"], 0.0)
        self.assertEqual(retrieved[2]["expected_net_credit"], 5000.0)

    def test_save_card_settlements_idempotence(self):
        # Save initial list
        settlements_1 = [
            {"machine_provider": "HDFC POS", "gross_swipes_copied": 1000.0}
        ]
        card_settlements.save_card_settlements("2026-06-01", settlements_1, db_path=self.test_db)
        
        # Verify initial save
        retrieved_1 = card_settlements.get_card_settlements_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(retrieved_1), 1)
        self.assertEqual(retrieved_1[0]["gross_swipes_copied"], 1000.0)
        
        # Save secondary list (override)
        settlements_2 = [
            {"machine_provider": "HDFC POS", "gross_swipes_copied": 2000.0},
            {"machine_provider": "SBI Touch", "gross_swipes_copied": 3000.0}
        ]
        card_settlements.save_card_settlements("2026-06-01", settlements_2, db_path=self.test_db)
        
        # Verify subsequent fetch returns only updated entries
        retrieved_2 = card_settlements.get_card_settlements_by_date("2026-06-01", db_path=self.test_db)
        self.assertEqual(len(retrieved_2), 2)
        self.assertEqual(retrieved_2[0]["gross_swipes_copied"], 2000.0)
        self.assertEqual(retrieved_2[1]["gross_swipes_copied"], 3000.0)

if __name__ == "__main__":
    unittest.main()
