import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

from ai_engine import analyze_register_sheet

class TestAIEngineOfflineFallback(unittest.TestCase):
    
    def test_offline_fallback_mode_on_exception(self):
        """Verifies that analyze_register_sheet gracefully falls back to offline mode when exception occurs."""
        # Using a nonexistent path will raise FileNotFoundError, triggering offline fallback block
        nonexistent_path = "/nonexistent/path/to/register_page.png"
        
        # Call analyze_register_sheet and verify the fallback output
        result = analyze_register_sheet(nonexistent_path)
        
        self.assertIsNotNone(result)
        self.assertTrue(result.get("offline_mode"))
        self.assertEqual(result.get("validation_status"), "offline_review")
        self.assertEqual(result.get("total_calculated_liters_hsd"), 0.0)
        self.assertEqual(result.get("total_calculated_liters_ms"), 0.0)
        self.assertEqual(result.get("total_cash_calculated"), 0.0)
        self.assertEqual(result.get("total_credit_sales"), 0.0)
        
        # Verify warnings notice
        warnings = result.get("mathematical_warnings", [])
        self.assertTrue(any("offline fallback" in w.lower() for w in warnings))
        
        # Check nozzles structure
        nozzles = result.get("nozzles", [])
        self.assertEqual(len(nozzles), 3)
        
        # Nozzle 1: MS-1 (Petrol)
        self.assertEqual(nozzles[0]["nozzle_name"], "MS-1 (Petrol)")
        self.assertEqual(nozzles[0]["fuel_type"], "REGULAR_MS")
        self.assertEqual(nozzles[0]["opening"], 0.0)
        self.assertEqual(nozzles[0]["closing"], 0.0)
        self.assertEqual(nozzles[0]["rate"], 106.31)
        self.assertEqual(nozzles[0]["net_sales_liters"], 0.0)
        
        # Nozzle 3: HSD-1 (Diesel)
        self.assertEqual(nozzles[2]["nozzle_name"], "HSD-1 (Diesel)")
        self.assertEqual(nozzles[2]["fuel_type"], "REGULAR_HSD")
        self.assertEqual(nozzles[2]["opening"], 0.0)
        self.assertEqual(nozzles[2]["closing"], 0.0)
        self.assertEqual(nozzles[2]["rate"], 94.27)
        self.assertEqual(nozzles[2]["net_sales_liters"], 0.0)

    @patch("ai_engine.run_gemini_vision_extraction")
    @patch("ai_engine.run_claude_accounting_guardrails")
    @patch("ai_engine.check_budget")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=MagicMock)
    @patch("rule_override.load_override_rules")
    def test_hard_overrides_integration(self, mock_rules, mock_open, mock_exists, mock_budget, mock_claude, mock_gemini):
        """Verifies that analyze_register_sheet applies hard overrides to the final audited payload."""
        mock_exists.return_value = True
        mock_budget.return_value = None
        mock_gemini.return_value = "Mock raw text containing गोपालराम"
        
        # Mock builtins.open return mock file context
        mock_file = MagicMock()
        mock_file.read.return_value = b"fake_image_bytes"
        mock_open.return_value.__enter__.return_value = mock_file
        
        # Mock load_override_rules to return test rules
        mock_rules.return_value = {
            "replacements": {
                "गोपालराम": "Gopalram Ji Dhaba",
                "जगबीर": "Jagveer Ji Dhaba"
            },
            "regex_patterns": {
                "व्हील\\s*न?\\s*(\\d+)": "Wheel No: \\1"
            }
        }
        
        # Claude returns a messy raw JSON payload
        mock_claude.return_value = {
            "date": "2026-06-25",
            "credit_sales": [
                {"party_name": "गोपालराम जी ढाबा", "vehicle_no": "व्हील न 90", "amount": 5000.0, "remarks": "Messy remarks"}
            ],
            "nozzles": []
        }
        
        # Trigger analyze_register_sheet
        result = analyze_register_sheet("mock_optimized_page.png")
        
        # Verify that programmatic overrides took absolute precedence!
        self.assertEqual(result["credit_sales"][0]["party_name"], "Gopalram Ji Dhaba")
        self.assertEqual(result["credit_sales"][0]["vehicle_no"], "Wheel No: 90")

if __name__ == "__main__":
    unittest.main()
