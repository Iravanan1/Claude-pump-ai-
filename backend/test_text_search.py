import os
import sys
import shutil
import sqlite3
import unittest
from fastapi.testclient import TestClient

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

import text_search
import init_db
import main

class TestFTS5SearchEngine(unittest.TestCase):
    def setUp(self):
        # Create test sandbox
        self.test_dir = os.path.join(BACKEND_DIR, "test_fts_sandbox")
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.test_db_path = os.path.join(self.test_dir, "test_ledger.db")
        
        # Save original db path and override
        self.original_init_db = init_db.DB_PATH
        self.original_main_db = main.DB_PATH
        
        init_db.DB_PATH = self.test_db_path
        main.DB_PATH = self.test_db_path
        
        # Initialize full database including FTS5 table
        init_db.initialize_database()
        
        # FastAPI client for route checking
        self.client = TestClient(main.app)

    def tearDown(self):
        # Restore db paths
        init_db.DB_PATH = self.original_init_db
        main.DB_PATH = self.original_main_db
        
        # Clean up test sandbox
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_fts_table_initialization(self):
        """Verifies that the fts virtual table ledger_fts structure is correctly created."""
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ledger_fts)")
        cols = [c[1] for c in cursor.fetchall()]
        conn.close()
        
        self.assertIn("date", cols)
        self.assertIn("raw_transcription_text", cols)

    def test_fts_index_sync_and_search(self):
        """Asserts that indexing raw text makes it searchable via MATCH query."""
        date_str = "2026-06-01"
        raw_text = "Date: 01-06-2026. Gopalram Transport RJ-14 credit sale amount 5000 tractor diesel."
        
        text_search.index_ledger_text(self.test_db_path, date_str, raw_text)
        
        # Search for Gopalram
        results = text_search.search_ledger_fts(self.test_db_path, "Gopalram")
        self.assertIn(date_str, results)
        
        # Search for RJ-14
        results2 = text_search.search_ledger_fts(self.test_db_path, "RJ-14")
        self.assertIn(date_str, results2)
        
        # Search for tractor
        results3 = text_search.search_ledger_fts(self.test_db_path, "tractor")
        self.assertIn(date_str, results3)
        
        # Search for non-existent keyword
        results_none = text_search.search_ledger_fts(self.test_db_path, "nonexistentkeyword")
        self.assertEqual(len(results_none), 0)

    def test_duplicate_indexing_override(self):
        """Verifies that indexing the same date twice overrides rather than duplicates entries."""
        date_str = "2026-06-01"
        text_search.index_ledger_text(self.test_db_path, date_str, "Initial raw text with keyword apples")
        text_search.index_ledger_text(self.test_db_path, date_str, "Updated raw text with keyword bananas")
        
        # Search for apples (should be deleted/overridden)
        results_apples = text_search.search_ledger_fts(self.test_db_path, "apples")
        self.assertEqual(len(results_apples), 0)
        
        # Search for bananas (should match)
        results_bananas = text_search.search_ledger_fts(self.test_db_path, "bananas")
        self.assertIn(date_str, results_bananas)

    def test_api_search_endpoint(self):
        """Checks GET /api/search/archive endpoint behavior."""
        date_str = "2026-06-02"
        text_search.index_ledger_text(self.test_db_path, date_str, "Test document containing keyword helicopter")
        
        # Hit the search API route
        response = self.client.get(f"/api/search/archive?q=helicopter")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("dates", data)
        self.assertIn(date_str, data["dates"])

if __name__ == "__main__":
    unittest.main()
