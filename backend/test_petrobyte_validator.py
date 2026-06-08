import os
import sys
import unittest
import pandas as pd
import sqlite3
import shutil

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

from petrobyte_validator import validate_petrobyte_csv_format

TEST_CSV = os.path.join(BACKEND_DIR, "test_petrobyte_format.csv")

class TestPetroByteValidator(unittest.TestCase):
    
    def tearDown(self):
        if os.path.exists(TEST_CSV):
            os.remove(TEST_CSV)
            
    def test_valid_balanced_csv(self):
        """Verifies that a valid and balanced double-entry CSV passes validation."""
        df = pd.DataFrame({
            "Date": ["2026-06-10", "2026-06-10", "2026-06-10", "2026-06-10"],
            "Ledger Name": ["Cash Sale", "Cash", "Bhim Singh", "Sales"],
            "Voucher Type": ["Receipt", "Receipt", "Sale", "Sale"],
            "Account Debit": [0.0, 26500.0, 12000.0, 0.0],
            "Account Credit": [26500.0, 0.0, 0.0, 12000.0],
            "Narration": ["Net Cash Sales", "Net Cash Sales", "Credit Sale", "Credit Sale"]
        })
        df.to_csv(TEST_CSV, index=False)
        
        result = validate_petrobyte_csv_format(TEST_CSV)
        self.assertTrue(result)
        
    def test_invalid_header_compliance(self):
        """Verifies that mismatched or missing columns raise ValueError."""
        # Missing Narration column
        df = pd.DataFrame({
            "Date": ["2026-06-10"],
            "Ledger Name": ["Cash Sale"],
            "Voucher Type": ["Receipt"],
            "Account Debit": [0.0],
            "Account Credit": [26500.0]
        })
        df.to_csv(TEST_CSV, index=False)
        
        with self.assertRaises(ValueError) as ctx:
            validate_petrobyte_csv_format(TEST_CSV)
            
        self.assertIn("Header alignment mismatch", str(ctx.exception))
        
    def test_invalid_voucher_types(self):
        """Verifies that unauthorized voucher types trigger failure."""
        df = pd.DataFrame({
            "Date": ["2026-06-10", "2026-06-10"],
            "Ledger Name": ["Cash Sale", "Cash"],
            "Voucher Type": ["Receipts", "Receipt"],  # 'Receipts' is invalid
            "Account Debit": [0.0, 26500.0],
            "Account Credit": [26500.0, 0.0],
            "Narration": ["Clean narration", "Clean narration"]
        })
        df.to_csv(TEST_CSV, index=False)
        
        with self.assertRaises(ValueError) as ctx:
            validate_petrobyte_csv_format(TEST_CSV)
            
        self.assertIn("Invalid Voucher Type", str(ctx.exception))
        self.assertIn("Receipts", str(ctx.exception))

    def test_unbalanced_block_verification(self):
        """Verifies that day block debits/credits mismatch raises error."""
        df = pd.DataFrame({
            "Date": ["2026-06-10", "2026-06-10"],
            "Ledger Name": ["Cash Sale", "Cash"],
            "Voucher Type": ["Receipt", "Receipt"],
            "Account Debit": [0.0, 26500.0],
            "Account Credit": [26505.50, 0.0],  # 5.50 mismatch
            "Narration": ["Clean narration", "Clean narration"]
        })
        df.to_csv(TEST_CSV, index=False)
        
        with self.assertRaises(ValueError) as ctx:
            validate_petrobyte_csv_format(TEST_CSV)
            
        self.assertIn("Mathematical imbalance detected", str(ctx.exception))
        self.assertIn("2026-06-10", str(ctx.exception))
        self.assertIn("Variance: ₹5.50", str(ctx.exception))

    def test_string_normalization_cleansing(self):
        """Verifies that trailing commas, returns, and double spaces in Narration are cleaned in-place."""
        df = pd.DataFrame({
            "Date": ["2026-06-10", "2026-06-10"],
            "Ledger Name": ["Cash Sale", "Cash"],
            "Voucher Type": ["Receipt", "Receipt"],
            "Account Debit": [0.0, 26500.0],
            "Account Credit": [26500.0, 0.0],
            "Narration": [
                "Net  Cash   Sales\r\nwith return,",  # double spaces, carriage return, trailing comma
                "Clean narration"
            ]
        })
        df.to_csv(TEST_CSV, index=False)
        
        result = validate_petrobyte_csv_format(TEST_CSV)
        self.assertTrue(result)
        
        # Verify in-place cleaning results
        df_cleaned = pd.read_csv(TEST_CSV)
        cleaned_narration_1 = df_cleaned.iloc[0]["Narration"]
        self.assertEqual(cleaned_narration_1, "Net Cash Sales with return")
        self.assertNotIn("\r", cleaned_narration_1)
        self.assertNotIn("\n", cleaned_narration_1)
        self.assertNotIn("  ", cleaned_narration_1)
        self.assertFalse(cleaned_narration_1.endswith(","))

if __name__ == "__main__":
    unittest.main()
