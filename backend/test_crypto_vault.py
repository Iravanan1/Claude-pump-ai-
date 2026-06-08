"""
Unit test suite for crypto_vault.py
"""

import os
import unittest
import tempfile
import shutil
from unittest.mock import patch

# Mock .env location to a temporary directory before importing crypto_vault
temp_dir = tempfile.mkdtemp()
temp_env_path = os.path.join(temp_dir, ".env")

with patch("crypto_vault.ENV_PATH", temp_env_path):
    import crypto_vault

class TestCryptoVault(unittest.TestCase):
    def setUp(self):
        # Set a predictable test master key
        os.environ["PUMP_AI_MASTER_KEY"] = "test_secret_password_123_456"
        # Reset internal cached fernet instance
        crypto_vault._fernet = None

    def tearDown(self):
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        crypto_vault._fernet = None

    @classmethod
    def tearDownClass(cls):
        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

    def test_key_derivation_and_caching(self):
        # Test that Fernet object is initialized and cached
        f1 = crypto_vault.get_fernet()
        f2 = crypto_vault.get_fernet()
        self.assertIsNotNone(f1)
        self.assertEqual(f1, f2)

    def test_encrypt_decrypt_string_roundtrip(self):
        original = "Ramesh Transport Services"
        ciphertext = crypto_vault.encrypt_field(original)
        self.assertNotEqual(original, ciphertext)
        self.assertTrue(crypto_vault.is_encrypted(ciphertext))
        
        decrypted = crypto_vault.decrypt_field(ciphertext, return_type=str)
        self.assertEqual(original, decrypted)

    def test_encrypt_decrypt_float_roundtrip(self):
        original = 15450.50
        ciphertext = crypto_vault.encrypt_field(original)
        self.assertNotEqual(str(original), ciphertext)
        self.assertTrue(crypto_vault.is_encrypted(ciphertext))
        
        decrypted = crypto_vault.decrypt_field(ciphertext, return_type=float)
        self.assertEqual(original, decrypted)

    def test_is_encrypted_checks(self):
        self.assertFalse(crypto_vault.is_encrypted(None))
        self.assertFalse(crypto_vault.is_encrypted(123.45))
        self.assertFalse(crypto_vault.is_encrypted("plain_text_name"))
        
        encrypted = crypto_vault.encrypt_field("secret")
        self.assertTrue(crypto_vault.is_encrypted(encrypted))

    def test_none_safety(self):
        self.assertIsNone(crypto_vault.encrypt_field(None))
        self.assertIsNone(crypto_vault.decrypt_field(None))

    def test_backward_compatibility(self):
        # If it is plain text, decrypt_field must return it as-is
        plain_str = "Legacy Unencrypted Party"
        self.assertEqual(crypto_vault.decrypt_field(plain_str, return_type=str), plain_str)
        
        plain_float = "2500.75"
        self.assertEqual(crypto_vault.decrypt_field(plain_float, return_type=float), 2500.75)
        
        # Test empty string / invalid input handling for numbers
        self.assertEqual(crypto_vault.decrypt_field("", return_type=float), 0.0)
        self.assertEqual(crypto_vault.decrypt_field("None", return_type=float), 0.0)

    def test_recursive_json_data_masking(self):
        raw_payload = {
            "date": "2026-05-30",
            "validation_status": "needs_review",
            "cash_short_or_over": -500.0,
            "credit_sales": [
                {"party_name": "Sharma Udhaar", "amount": 8000.0, "vehicle_no": "HR-26-1234"},
                {"party_name": "Verma Ji", "amount": 2500.0, "vehicle_no": "DL-1C-5678"}
            ],
            "cash_expenses": [
                {"party_name": "Office Tea", "amount": 150.0}
            ],
            "nested_level": {
                "party_name": "Deep Nested Party",
                "amount": 999.99
            }
        }
        
        # Encrypt the dict recursively
        encrypted_dict = crypto_vault.encrypt_raw_data(raw_payload)
        
        # Check that sensitive fields are masked on disk representation
        self.assertTrue(crypto_vault.is_encrypted(encrypted_dict["cash_short_or_over"]))
        self.assertTrue(crypto_vault.is_encrypted(encrypted_dict["credit_sales"][0]["party_name"]))
        self.assertTrue(crypto_vault.is_encrypted(encrypted_dict["credit_sales"][0]["amount"]))
        self.assertTrue(crypto_vault.is_encrypted(encrypted_dict["nested_level"]["party_name"]))
        
        # Check that non-sensitive fields are untouched
        self.assertEqual(encrypted_dict["date"], "2026-05-30")
        self.assertEqual(encrypted_dict["credit_sales"][0]["vehicle_no"], "HR-26-1234")
        
        # Decrypt the dict recursively
        decrypted_dict = crypto_vault.decrypt_raw_data(encrypted_dict)
        
        # Ensure it matches the original completely
        self.assertEqual(decrypted_dict["cash_short_or_over"], -500.0)
        self.assertEqual(decrypted_dict["credit_sales"][0]["party_name"], "Sharma Udhaar")
        self.assertEqual(decrypted_dict["credit_sales"][0]["amount"], 8000.0)
        self.assertEqual(decrypted_dict["cash_expenses"][0]["party_name"], "Office Tea")
        self.assertEqual(decrypted_dict["cash_expenses"][0]["amount"], 150.0)
        self.assertEqual(decrypted_dict["nested_level"]["party_name"], "Deep Nested Party")
        self.assertEqual(decrypted_dict["nested_level"]["amount"], 999.99)

    def test_auto_generate_key_when_missing(self):
        # Force missing key from env and temp env file
        if "PUMP_AI_MASTER_KEY" in os.environ:
            del os.environ["PUMP_AI_MASTER_KEY"]
        if os.path.exists(temp_env_path):
            os.remove(temp_env_path)
            
        crypto_vault._fernet = None
        
        with patch("crypto_vault.ENV_PATH", temp_env_path):
            # Calling get_fernet will trigger auto-generation and write to file
            fernet_instance = crypto_vault.get_fernet()
            self.assertIsNotNone(fernet_instance)
            
            # Assert file is written and environment variable is populated
            self.assertTrue(os.path.exists(temp_env_path))
            self.assertIn("PUMP_AI_MASTER_KEY", os.environ)
            
            # Read from temp file to check content
            with open(temp_env_path, "r") as f:
                content = f.read()
            self.assertIn("PUMP_AI_MASTER_KEY=", content)

if __name__ == "__main__":
    unittest.main()
