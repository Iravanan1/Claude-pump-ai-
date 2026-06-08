import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import learning_cache
import main
import ai_engine

class TestLearningCache(unittest.TestCase):
    def setUp(self):
        # Override the corrections cache path to avoid polluting production cache
        self.original_cache_path = learning_cache.CACHE_PATH
        self.test_cache_path = os.path.join(BACKEND_DIR, "test_corrections_cache.json")
        learning_cache.CACHE_PATH = self.test_cache_path
        
        # Clean up test cache if it exists
        if os.path.exists(self.test_cache_path):
            os.remove(self.test_cache_path)
            
        # Setup TestClient
        self.client = TestClient(main.app)

    def tearDown(self):
        # Restore original cache path
        learning_cache.CACHE_PATH = self.original_cache_path
        
        # Clean up files
        if os.path.exists(self.test_cache_path):
            os.remove(self.test_cache_path)

    def test_record_correction_and_increment(self):
        """Verifies that recording corrections saves to JSON and tracks recurrence count."""
        # 1. Record first correction
        learning_cache.HandwritingMemory.record_correction("बील न", "vehicle_no", "व्हील न.")
        
        # Check cache state
        self.assertTrue(os.path.exists(self.test_cache_path))
        with open(self.test_cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        self.assertIn("बील न_vehicle_no", data["corrections"])
        entry = data["corrections"]["बील न_vehicle_no"]
        self.assertEqual(entry["original"], "बील न")
        self.assertEqual(entry["corrected"], "व्हील न.")
        self.assertEqual(entry["count"], 1)
        
        # 2. Record same correction again (increment count)
        learning_cache.HandwritingMemory.record_correction("बील न", "vehicle_no", "व्हील न.")
        
        with open(self.test_cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["corrections"]["बील न_vehicle_no"]["count"], 2)

    def test_get_injected_context_prompt(self):
        """Verifies that top corrected terms are formatted correctly in the prompt block."""
        # Record multiple corrections with different counts
        learning_cache.HandwritingMemory.record_correction("gopalram ji chaba", "party_name", "गोपालराम जी ढाबा")
        learning_cache.HandwritingMemory.record_correction("बील न", "vehicle_no", "व्हील न.")
        
        # Increment one of them so it has higher count
        learning_cache.HandwritingMemory.record_correction("बील न", "vehicle_no", "व्हील न.")
        
        # Get prompt block
        prompt_block = learning_cache.HandwritingMemory.get_injected_context_prompt()
        
        self.assertIn("INJECTED CONTINUOUS LEARNING CONTEXT", prompt_block)
        self.assertIn("व्हील न.", prompt_block)
        self.assertIn("गोपालराम जी ढाबा", prompt_block)
        
        # Verify sorting (बील न is count 2, gopalram is count 1)
        # So 'बील न' should appear before 'gopalram'
        idx_wheel = prompt_block.index("व्हील न.")
        idx_dhaba = prompt_block.index("गोपालराम जी ढाबा")
        self.assertTrue(idx_wheel < idx_dhaba, "Higher count corrections must appear first")

    def test_record_correction_api(self):
        """Verifies that the /api/record-correction route updates the learning cache."""
        payload = {
            "original_raw_text": "testing 5 ltr",
            "field_type": "nozzle_reading",
            "corrected_text": "Testing (5L)"
        }
        
        response = self.client.post("/api/record-correction", json=payload)
        self.assertEqual(response.status_code, 200)
        
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(json_data["original"], "testing 5 ltr")
        self.assertEqual(json_data["corrected"], "Testing (5L)")
        
        # Check local cache file
        with open(self.test_cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = "testing 5 ltr_nozzle_reading"
        self.assertIn(key, data["corrections"])
        self.assertEqual(data["corrections"][key]["count"], 1)

    @patch("google.genai.Client")
    def test_ai_engine_prompt_injection(self, mock_genai):
        """Verifies that ai_engine calls get_injected_context_prompt and includes it in Gemini call."""
        # Add a correction to test cache
        learning_cache.HandwritingMemory.record_correction("बील न", "vehicle_no", "व्हील न.")
        
        # Set environment variable mock to bypass key check
        os.environ["GEMINI_API_KEY"] = "mock_key"
        
        # Mock client models call
        mock_client_instance = MagicMock()
        mock_genai.return_value = mock_client_instance
        
        # Execute vision call and expect it to parse the prompt
        try:
            ai_engine.run_gemini_vision_extraction(b"fake_image_bytes")
        except Exception:
            # We expect it to fail downstream because of mocked response, but we check if prompt was generated
            pass
            
        # Verify mock_client_instance.models.generate_content was called and the prompt argument included our injected correction
        called_args = mock_client_instance.models.generate_content.call_args
        self.assertIsNotNone(called_args)
        
        # The prompt is the second argument in generate_content (contents list contains Part and prompt)
        contents = called_args[1]["contents"]
        prompt_arg = contents[1]
        
        self.assertIn("INJECTED CONTINUOUS LEARNING CONTEXT", prompt_arg)
        self.assertIn("व्हील न.", prompt_arg)

if __name__ == "__main__":
    unittest.main()
