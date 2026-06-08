"""
Unit tests for rule_override.py
"""

import os
import unittest
import tempfile
import json
import shutil

import rule_override

class TestRuleOverride(unittest.TestCase):
    def setUp(self):
        # Create isolated temporary rules file
        self.test_dir = tempfile.mkdtemp()
        self.rules_file = os.path.join(self.test_dir, "test_rules.json")
        
        # Structure matching prompt requirements
        rules_data = {
            "replacements": {
                "गोपालराम": "Gopalram Ji Dhaba",
                "जगबीर": "Jagveer Ji Dhaba",
                "पेटीएम sbi": "Paytm Bank Drop to SBI"
            },
            "regex_patterns": {
                "व्हील\\s*न?\\s*(\\d+)": "Wheel No: \\1"
            }
        }
        
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(rules_data, f)

    def tearDown(self):
        # Cleanup
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_exact_and_substring_replacements(self):
        # Check exact and substring replacement triggers
        payload = {
            "credit_sales": [
                {"party_name": "गोपालराम जी ढाबा", "vehicle_no": "HR-55", "amount": 5000.0, "remarks": "go to गोपालराम"},
                {"party_name": "जगबीर", "vehicle_no": "N/A", "amount": 2500.0, "remarks": "Lunch expense"}
            ],
            "cash_expenses": [
                {"party_name": "पेटीएम SBI", "amount": 10000.0, "remarks": "Deposited to SBI bank"}
            ]
        }
        
        overridden = rule_override.apply_hard_overrides(payload, rules_path=self.rules_file)
        
        # Gopalram Ji Dhaba checks
        self.assertEqual(overridden["credit_sales"][0]["party_name"], "Gopalram Ji Dhaba")
        self.assertEqual(overridden["credit_sales"][0]["remarks"], "Gopalram Ji Dhaba") # "गोपालराम" is replacement trigger
        
        # Jagveer Ji Dhaba checks
        self.assertEqual(overridden["credit_sales"][1]["party_name"], "Jagveer Ji Dhaba")
        
        # Paytm Bank Drop checks
        self.assertEqual(overridden["cash_expenses"][0]["party_name"], "Paytm Bank Drop to SBI")

    def test_regex_matching_patterns(self):
        # Check vehicle number regex transformations
        payload = {
            "credit_sales": [
                {"party_name": "Ramesh", "vehicle_no": "व्हील न 45", "amount": 1000.0, "remarks": "व्हील न 45 diesel"}
            ]
        }
        
        overridden = rule_override.apply_hard_overrides(payload, rules_path=self.rules_file)
        
        # Expecting regex groups captures to resolve in clean English Wheel tags
        self.assertEqual(overridden["credit_sales"][0]["vehicle_no"], "Wheel No: 45")
        self.assertEqual(overridden["credit_sales"][0]["remarks"], "Wheel No: 45 diesel")

if __name__ == "__main__":
    unittest.main()
