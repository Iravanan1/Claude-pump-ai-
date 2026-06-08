import os
import sys
import sqlite3
import pandas as pd
import unittest
from unittest.mock import patch

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import price_registry

class TestPriceRegistry(unittest.TestCase):
    def setUp(self):
        self.original_db = price_registry.DB_PATH
        self.test_db = os.path.join(BACKEND_DIR, "test_rates.db")
        price_registry.DB_PATH = self.test_db
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
            
        # Initialize
        price_registry.init_rates_db()

    def tearDown(self):
        price_registry.DB_PATH = self.original_db
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_init_and_get_rates(self):
        """Verifies that fuel_rates table is initialized and query returns correctly."""
        # Insert raw rate
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO fuel_rates (date, hsd_rate, ms_rate) VALUES (?, ?, ?)", ("2026-05-30", 91.60, 106.19))
        conn.commit()
        conn.close()
        
        rates = price_registry.get_rates_for_date("2026-05-30")
        self.assertIsNotNone(rates)
        self.assertEqual(rates["hsd_rate"], 91.60)
        self.assertEqual(rates["ms_rate"], 106.19)
        
        # Test nonexistent date
        self.assertIsNone(price_registry.get_rates_for_date("2026-05-31"))

    def test_import_rate_csv(self):
        """Verifies that import_rate_csv loads data from a CSV spreadsheet correctly."""
        csv_path = os.path.join(BACKEND_DIR, "test_rates.csv")
        
        # Create a basic test CSV
        df = pd.DataFrame({
            "date": ["2026-06-01", "2026-06-02"],
            "hsd_rate": [92.00, 92.50],
            "ms_rate": [107.00, 107.50]
        })
        df.to_csv(csv_path, index=False)
        
        try:
            # Import
            price_registry.import_rate_csv(csv_path)
            
            # Verify date 1
            rates_1 = price_registry.get_rates_for_date("2026-06-01")
            self.assertIsNotNone(rates_1)
            self.assertEqual(rates_1["hsd_rate"], 92.00)
            self.assertEqual(rates_1["ms_rate"], 107.00)
            
            # Verify date 2
            rates_2 = price_registry.get_rates_for_date("2026-06-02")
            self.assertIsNotNone(rates_2)
            self.assertEqual(rates_2["hsd_rate"], 92.50)
            self.assertEqual(rates_2["ms_rate"], 107.50)
            
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

if __name__ == "__main__":
    unittest.main()
