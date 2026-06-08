"""
test_cost_tracker.py — Unit Tests for /backend/cost_tracker.py
==============================================================
Covers:
  1. Schema:          init_cost_db() creates the api_usage table.
  2. Cost calc:       _calculate_cost() applies the correct per-provider rates.
  3. Token extract:   _extract_tokens() reads Anthropic and Gemini response objects.
  4. Logging:         log_api_transaction() writes a row with correct values.
  5. Budget check:    check_budget() passes under cap, raises when over cap.
  6. get_today_spend: returns correct rolling sum from the DB.
  7. Usage summary:   get_usage_summary() groups by date + provider correctly.
  8. BudgetExceededError: message and attributes are correct.
  9. ai_engine integration: analyze_register_sheet() returns offline template
                             when BudgetExceededError is raised pre-flight.
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ── Helpers ───────────────────────────────────────────────────────────────

def _tmp_db() -> str:
    """Creates an isolated temporary SQLite file and returns its path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _mock_anthropic_response(input_tokens: int, output_tokens: int, model="claude-3-5-sonnet"):
    r = MagicMock()
    r.model = model
    r.usage = MagicMock()
    r.usage.input_tokens  = input_tokens
    r.usage.output_tokens = output_tokens
    return r


def _mock_gemini_response(prompt_tokens: int, candidate_tokens: int, model="gemini-1.5-flash"):
    r = MagicMock()
    r.model = model
    r.usage_metadata = MagicMock()
    r.usage_metadata.prompt_token_count     = prompt_tokens
    r.usage_metadata.candidates_token_count = candidate_tokens
    return r


# ── Import under test ─────────────────────────────────────────────────────
from cost_tracker import (
    init_cost_db,
    log_api_transaction,
    get_today_spend,
    check_budget,
    get_usage_summary,
    BudgetExceededError,
    _calculate_cost,
    _extract_tokens,
    DAILY_BUDGET_CAP_USD,
)


# =========================================================================
# 1. Schema Tests
# =========================================================================

class TestInitCostDb(unittest.TestCase):

    def test_table_created(self):
        db = _tmp_db()
        try:
            init_cost_db(db_path=db)
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='api_usage'"
            ).fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
        finally:
            os.unlink(db)

    def test_idempotent(self):
        """Calling init_cost_db twice should not raise."""
        db = _tmp_db()
        try:
            init_cost_db(db_path=db)
            init_cost_db(db_path=db)  # second call — must be safe
        finally:
            os.unlink(db)

    def test_expected_columns(self):
        db = _tmp_db()
        try:
            init_cost_db(db_path=db)
            conn = sqlite3.connect(db)
            cols = [row[1] for row in conn.execute("PRAGMA table_info(api_usage)").fetchall()]
            conn.close()
            for expected in ("date", "provider", "model",
                             "input_tokens", "output_tokens", "calculated_cost_usd"):
                self.assertIn(expected, cols)
        finally:
            os.unlink(db)


# =========================================================================
# 2. Cost Calculation Tests
# =========================================================================

class TestCalculateCost(unittest.TestCase):

    def test_gemini_cost_zero_tokens(self):
        self.assertEqual(_calculate_cost("gemini", 0, 0), 0.0)

    def test_anthropic_cost_zero_tokens(self):
        self.assertEqual(_calculate_cost("anthropic", 0, 0), 0.0)

    def test_gemini_cost_1M_input(self):
        # 1 000 000 input tokens at $0.075 / 1M → $0.075
        cost = _calculate_cost("gemini", 1_000_000, 0)
        self.assertAlmostEqual(cost, 0.075, places=5)

    def test_anthropic_cost_1M_input(self):
        # 1 000 000 input tokens at $3.00 / 1M → $3.00
        cost = _calculate_cost("anthropic", 1_000_000, 0)
        self.assertAlmostEqual(cost, 3.00, places=5)

    def test_anthropic_cost_1M_output(self):
        # 1 000 000 output tokens at $15.00 / 1M → $15.00
        cost = _calculate_cost("anthropic", 0, 1_000_000)
        self.assertAlmostEqual(cost, 15.00, places=5)

    def test_unknown_provider_returns_zero(self):
        cost = _calculate_cost("openai", 1000, 500)
        self.assertEqual(cost, 0.0)

    def test_case_insensitive_provider(self):
        cost_lower = _calculate_cost("gemini",  1000, 1000)
        cost_upper = _calculate_cost("GEMINI",  1000, 1000)
        self.assertEqual(cost_lower, cost_upper)

    def test_mixed_tokens(self):
        # 2000 input + 1000 output for Gemini using per-token rates
        expected = (2000 * (0.075 / 1_000_000)) + (1000 * (0.300 / 1_000_000))
        cost = _calculate_cost("gemini", 2000, 1000)
        self.assertAlmostEqual(cost, expected, places=8)


# =========================================================================
# 3. Token Extraction Tests
# =========================================================================

class TestExtractTokens(unittest.TestCase):

    def test_anthropic_extracts_tokens(self):
        r = _mock_anthropic_response(500, 200)
        inp, out, model = _extract_tokens("anthropic", r)
        self.assertEqual(inp,  500)
        self.assertEqual(out,  200)
        self.assertIn("claude", model.lower())

    def test_gemini_extracts_tokens(self):
        r = _mock_gemini_response(1000, 300)
        inp, out, model = _extract_tokens("gemini", r)
        self.assertEqual(inp,  1000)
        self.assertEqual(out,  300)
        self.assertIn("gemini", model.lower())

    def test_missing_usage_returns_zeros(self):
        # MagicMock with empty spec — getattr returns another MagicMock (truthy),
        # but integer operations on it will raise, so _extract_tokens catches and
        # falls back. Verify tokens are 0.
        r = MagicMock(spec=[])
        inp, out, _ = _extract_tokens("anthropic", r)
        self.assertEqual(inp, 0)
        self.assertEqual(out, 0)

    def test_unknown_provider_returns_zeros(self):
        r = MagicMock()
        inp, out, model = _extract_tokens("openai", r)
        self.assertEqual(inp,  0)
        self.assertEqual(out,  0)

    def test_none_token_values_fall_back_to_zero(self):
        """None attribute values should be treated as 0, not raise TypeError."""
        r = MagicMock()
        r.usage.input_tokens  = None
        r.usage.output_tokens = None
        inp, out, _ = _extract_tokens("anthropic", r)
        self.assertEqual(inp,  0)
        self.assertEqual(out,  0)


# =========================================================================
# 4. log_api_transaction Tests
# =========================================================================

class TestLogApiTransaction(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_cost_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_log_anthropic_writes_row(self):
        r = _mock_anthropic_response(400, 100)
        cost = log_api_transaction("anthropic", r, db_path=self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT * FROM api_usage").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[2], "anthropic")   # provider column
        self.assertEqual(row[4], 400)            # input_tokens
        self.assertEqual(row[5], 100)            # output_tokens
        self.assertGreater(row[6], 0.0)          # calculated_cost_usd
        self.assertAlmostEqual(cost, row[6], places=8)

    def test_log_gemini_writes_row(self):
        r = _mock_gemini_response(800, 200)
        log_api_transaction("gemini", r, db_path=self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT provider, input_tokens FROM api_usage").fetchone()
        conn.close()

        self.assertEqual(row[0], "gemini")
        self.assertEqual(row[1], 800)

    def test_override_tokens_used_when_provided(self):
        """Explicit input/output_tokens kwargs must override auto-extraction."""
        r = _mock_anthropic_response(9999, 9999)  # these should be ignored
        log_api_transaction("anthropic", r, db_path=self.db,
                            input_tokens=10, output_tokens=5)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT input_tokens, output_tokens FROM api_usage").fetchone()
        conn.close()

        self.assertEqual(row[0], 10)
        self.assertEqual(row[1],  5)

    def test_returns_cost_float(self):
        r = _mock_gemini_response(1000, 500)
        cost = log_api_transaction("gemini", r, db_path=self.db)
        self.assertIsInstance(cost, float)
        self.assertGreater(cost, 0.0)

    def test_multiple_rows_accumulated(self):
        for _ in range(3):
            log_api_transaction("gemini",
                                _mock_gemini_response(100, 50),
                                db_path=self.db)

        conn = sqlite3.connect(self.db)
        count = conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0]
        conn.close()
        self.assertEqual(count, 3)


# =========================================================================
# 5. check_budget Tests
# =========================================================================

class TestCheckBudget(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_cost_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def _inject_spend(self, amount_usd: float):
        """Directly inserts a spend row for today."""
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO api_usage (date, provider, model, input_tokens, output_tokens, calculated_cost_usd) "
            "VALUES (?, 'gemini', 'test', 0, 0, ?)",
            (str(date.today()), amount_usd),
        )
        conn.commit()
        conn.close()

    @patch("cost_tracker.DAILY_BUDGET_CAP_USD", 5.00)
    def test_under_cap_does_not_raise(self):
        self._inject_spend(2.50)
        # Should return the spend amount without raising
        spent = check_budget(db_path=self.db)
        self.assertAlmostEqual(spent, 2.50, places=4)

    @patch("cost_tracker.DAILY_BUDGET_CAP_USD", 5.00)
    def test_at_cap_raises(self):
        self._inject_spend(5.00)
        with self.assertRaises(BudgetExceededError):
            check_budget(db_path=self.db)

    @patch("cost_tracker.DAILY_BUDGET_CAP_USD", 5.00)
    def test_over_cap_raises(self):
        self._inject_spend(7.50)
        with self.assertRaises(BudgetExceededError):
            check_budget(db_path=self.db)

    @patch("cost_tracker.DAILY_BUDGET_CAP_USD", 5.00)
    def test_exception_contains_spend_and_cap(self):
        self._inject_spend(6.00)
        try:
            check_budget(db_path=self.db)
            self.fail("BudgetExceededError not raised")
        except BudgetExceededError as exc:
            self.assertAlmostEqual(exc.spent, 6.00, places=4)
            self.assertAlmostEqual(exc.cap,   5.00, places=4)

    @patch("cost_tracker.DAILY_BUDGET_CAP_USD", 5.00)
    def test_yesterday_spend_does_not_count(self):
        """Spend from a previous day must not trigger today's cap."""
        yesterday = str(date.today() - timedelta(days=1))
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO api_usage (date, provider, model, input_tokens, output_tokens, calculated_cost_usd) "
            "VALUES (?, 'gemini', 'test', 0, 0, 10.00)",
            (yesterday,),
        )
        conn.commit()
        conn.close()
        # today is still $0 — should not raise
        spent = check_budget(db_path=self.db)
        self.assertAlmostEqual(spent, 0.0, places=4)


# =========================================================================
# 6. get_today_spend Tests
# =========================================================================

class TestGetTodaySpend(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_cost_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_empty_db_returns_zero(self):
        self.assertEqual(get_today_spend(db_path=self.db), 0.0)

    def test_sum_of_todays_rows(self):
        for amt in (1.0, 0.5, 0.25):
            conn = sqlite3.connect(self.db)
            conn.execute(
                "INSERT INTO api_usage (date, provider, model, input_tokens, output_tokens, calculated_cost_usd) "
                "VALUES (?, 'anthropic', 'claude', 0, 0, ?)",
                (str(date.today()), amt),
            )
            conn.commit()
            conn.close()
        self.assertAlmostEqual(get_today_spend(db_path=self.db), 1.75, places=6)

    def test_excludes_previous_days(self):
        yesterday = str(date.today() - timedelta(days=1))
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO api_usage (date, provider, model, input_tokens, output_tokens, calculated_cost_usd) "
            "VALUES (?, 'gemini', 'test', 0, 0, 99.0)",
            (yesterday,),
        )
        conn.commit()
        conn.close()
        self.assertAlmostEqual(get_today_spend(db_path=self.db), 0.0, places=6)


# =========================================================================
# 7. get_usage_summary Tests
# =========================================================================

class TestGetUsageSummary(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_cost_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def _insert(self, day_offset, provider, input_tk, output_tk, cost):
        day = str(date.today() - timedelta(days=day_offset))
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO api_usage (date, provider, model, input_tokens, output_tokens, calculated_cost_usd) "
            "VALUES (?, ?, 'test', ?, ?, ?)",
            (day, provider, input_tk, output_tk, cost),
        )
        conn.commit()
        conn.close()

    def test_empty_returns_empty_list(self):
        self.assertEqual(get_usage_summary(days=7, db_path=self.db), [])

    def test_today_row_included(self):
        self._insert(0, "gemini", 100, 50, 0.01)
        rows = get_usage_summary(days=1, db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "gemini")

    def test_old_rows_excluded(self):
        self._insert(10, "anthropic", 100, 50, 0.50)
        rows = get_usage_summary(days=7, db_path=self.db)
        self.assertEqual(rows, [])

    def test_grouped_by_provider(self):
        self._insert(0, "gemini",    100, 50, 0.01)
        self._insert(0, "anthropic", 200, 80, 0.50)
        rows = get_usage_summary(days=1, db_path=self.db)
        providers = {r["provider"] for r in rows}
        self.assertIn("gemini",    providers)
        self.assertIn("anthropic", providers)

    def test_costs_aggregated(self):
        self._insert(0, "gemini", 100, 50, 0.010)
        self._insert(0, "gemini", 200, 80, 0.025)
        rows = get_usage_summary(days=1, db_path=self.db)
        gemini_row = next(r for r in rows if r["provider"] == "gemini")
        self.assertAlmostEqual(gemini_row["total_cost_usd"], 0.035, places=6)


# =========================================================================
# 8. BudgetExceededError Tests
# =========================================================================

class TestBudgetExceededError(unittest.TestCase):

    def test_attributes(self):
        exc = BudgetExceededError(spent=6.12, cap=5.00)
        self.assertAlmostEqual(exc.spent, 6.12, places=4)
        self.assertAlmostEqual(exc.cap,   5.00, places=4)

    def test_is_runtime_error(self):
        self.assertIsInstance(BudgetExceededError(1.0, 2.0), RuntimeError)

    def test_message_contains_key_phrases(self):
        msg = str(BudgetExceededError(5.50, 5.00))
        self.assertIn("Daily API budget limit reached", msg)
        self.assertIn("offline", msg.lower())


# =========================================================================
# 9. ai_engine Integration Tests
# =========================================================================

class TestAiEngineIntegration(unittest.TestCase):
    """
    Ensures analyze_register_sheet() returns the offline template with the
    correct budget-exceeded warning when check_budget() raises.
    """

    @patch("ai_engine.check_budget", side_effect=BudgetExceededError(5.50, 5.00))
    @patch("os.path.exists", return_value=True)
    def test_budget_exceeded_returns_offline_template(self, mock_exists, mock_budget):
        from ai_engine import analyze_register_sheet
        result = analyze_register_sheet("/fake/image.png")

        self.assertTrue(result.get("offline_mode"), True)
        self.assertEqual(result.get("validation_status"), "offline_review")
        warnings = result.get("mathematical_warnings", [])
        self.assertTrue(len(warnings) > 0)
        self.assertIn("budget", warnings[0].lower())

    @patch("ai_engine.check_budget", return_value=2.50)
    @patch("ai_engine.run_gemini_vision_extraction", side_effect=RuntimeError("network error"))
    @patch("os.path.exists", return_value=True)
    def test_network_error_still_returns_offline_template(self, mock_exists, mock_gemini, mock_budget):
        from ai_engine import analyze_register_sheet
        result = analyze_register_sheet("/fake/image.png")
        self.assertTrue(result.get("offline_mode"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
