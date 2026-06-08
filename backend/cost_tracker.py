"""
cost_tracker.py — API Cost Accounting Module
=============================================
Tracks per-call token usage and running cost for every Gemini and Anthropic
API transaction. Enforces a configurable daily budget cap that, when breached,
raises BudgetExceededError so the caller can fall back to offline mode.

Pricing reference (update whenever providers change rates):
  Gemini 1.5 Flash  : $0.075 / 1M input tokens,  $0.30  / 1M output tokens
  Claude 3.5 Sonnet : $3.00  / 1M input tokens,  $15.00 / 1M output tokens

Usage:
    from cost_tracker import log_api_transaction, check_budget, BudgetExceededError

    # Before calling a cloud API:
    check_budget()                        # raises BudgetExceededError if over cap

    # After a successful API response:
    log_api_transaction("gemini",  response)
    log_api_transaction("anthropic", response)
"""

import sqlite3
import os
import logging
from datetime import date as _date
from typing import Optional

# Project-wide structured logger
try:
    from logger import logger  # type: ignore
except ImportError:
    logger = logging.getLogger("cost_tracker")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

# Hard daily spend cap in USD. Change this to adjust the safety ceiling.
DAILY_BUDGET_CAP_USD: float = float(os.getenv("PUMPAI_DAILY_BUDGET_USD", "5.00"))

# Pricing per token (price_per_million / 1_000_000).
# Source: Google AI & Anthropic pricing pages (May 2025).
_PRICING: dict[str, dict[str, float]] = {
    "gemini": {
        # Gemini 1.5 Flash: $0.075 / 1M input,  $0.30 / 1M output
        "input_per_token":  0.075  / 1_000_000,
        "output_per_token": 0.300  / 1_000_000,
    },
    "anthropic": {
        # Claude 3.5 Sonnet: $3.00 / 1M input, $15.00 / 1M output
        "input_per_token":  3.00   / 1_000_000,
        "output_per_token": 15.00  / 1_000_000,
    },
}


# ---------------------------------------------------------------------------
# Custom Exception
# ---------------------------------------------------------------------------

class BudgetExceededError(RuntimeError):
    """Raised when the day's accumulated API spend reaches DAILY_BUDGET_CAP_USD."""
    def __init__(self, spent: float, cap: float):
        self.spent = spent
        self.cap   = cap
        super().__init__(
            f"Daily API budget limit reached (${spent:.4f} / ${cap:.2f}). "
            "Switched to offline-only extraction to protect accounts."
        )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_cost_db(db_path: str = DB_PATH) -> None:
    """
    Creates the `api_usage` table if it does not already exist.
    Called once on module import and at FastAPI startup.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                date                TEXT    NOT NULL,
                provider            TEXT    NOT NULL,
                model               TEXT,
                input_tokens        INTEGER NOT NULL DEFAULT 0,
                output_tokens       INTEGER NOT NULL DEFAULT 0,
                calculated_cost_usd REAL    NOT NULL DEFAULT 0.0,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.debug("cost_tracker: api_usage table ready.")
    except Exception as e:
        logger.error(f"cost_tracker: failed to initialise api_usage table — {e}")


# Initialise table on import so there is never a chicken-and-egg issue.
init_cost_db()


# ---------------------------------------------------------------------------
# Cost Calculation
# ---------------------------------------------------------------------------

def _calculate_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    """
    Multiplies token counts by the provider's per-token price.
    Returns the cost in USD.
    """
    provider = provider.lower()
    if provider not in _PRICING:
        logger.warning(f"cost_tracker: unknown provider '{provider}' — cost recorded as 0.")
        return 0.0
    rates = _PRICING[provider]
    cost = (input_tokens  * rates["input_per_token"]  +
            output_tokens * rates["output_per_token"])
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Token Extraction
# ---------------------------------------------------------------------------

def _extract_tokens(provider: str, response_obj) -> tuple[int, int, str]:
    """
    Extracts (input_tokens, output_tokens, model_name) from a provider
    response object.

    Handles:
      - Anthropic  : response.usage.input_tokens / output_tokens
                     response.model
      - Gemini SDK : response.usage_metadata.prompt_token_count /
                     candidates_token_count / total_token_count
                     response.model (may not be present on all versions)
    Falls back to 0 / 0 / 'unknown' if attributes are missing.
    """
    input_tk = 0
    output_tk = 0
    model_name = "unknown"

    provider = provider.lower()
    try:
        if provider == "anthropic":
            usage = getattr(response_obj, "usage", None)
            if usage:
                input_tk  = getattr(usage, "input_tokens",  0) or 0
                output_tk = getattr(usage, "output_tokens", 0) or 0
            model_name = getattr(response_obj, "model", "claude-3-5-sonnet")

        elif provider == "gemini":
            usage = getattr(response_obj, "usage_metadata", None)
            if usage:
                input_tk  = getattr(usage, "prompt_token_count",     0) or 0
                output_tk = getattr(usage, "candidates_token_count", 0) or 0
            model_name = getattr(response_obj, "model", "gemini-1.5-flash")

    except Exception as e:
        logger.warning(f"cost_tracker: token extraction error ({provider}) — {e}")

    return input_tk, output_tk, model_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_api_transaction(
    provider: str,
    response_obj,
    db_path: str = DB_PATH,
    *,
    input_tokens:  Optional[int] = None,
    output_tokens: Optional[int] = None,
    model:         Optional[str] = None,
) -> float:
    """
    Extracts token counts from `response_obj`, calculates the cost, and
    writes a row to `api_usage`.

    Args:
        provider:      "gemini" or "anthropic" (case-insensitive).
        response_obj:  The raw API response object.
        db_path:       SQLite database path (default: project ledger.db).
        input_tokens:  Override input token count (skips auto-extraction).
        output_tokens: Override output token count (skips auto-extraction).
        model:         Override model name string.

    Returns:
        float: Calculated cost in USD for this transaction.
    """
    if input_tokens is None or output_tokens is None:
        _in, _out, _model = _extract_tokens(provider, response_obj)
        input_tokens  = input_tokens  if input_tokens  is not None else _in
        output_tokens = output_tokens if output_tokens is not None else _out
        if model is None:
            model = _model

    cost = _calculate_cost(provider, input_tokens, output_tokens)
    today = str(_date.today())

    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO api_usage
                (date, provider, model, input_tokens, output_tokens, calculated_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (today, provider.lower(), model or "unknown",
             input_tokens, output_tokens, cost),
        )
        conn.commit()
        conn.close()
        logger.info(
            f"cost_tracker: {provider} | in={input_tokens} out={output_tokens} "
            f"| cost=${cost:.6f} | daily budget used: ${get_today_spend(db_path):.4f} / ${DAILY_BUDGET_CAP_USD:.2f}"
        )
    except Exception as e:
        logger.error(f"cost_tracker: failed to log transaction — {e}")

    return cost


def get_today_spend(db_path: str = DB_PATH) -> float:
    """
    Returns the total USD spent on API calls so far today.
    """
    today = str(_date.today())
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT COALESCE(SUM(calculated_cost_usd), 0.0) FROM api_usage WHERE date = ?",
            (today,),
        ).fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"cost_tracker: failed to query today's spend — {e}")
        return 0.0


def check_budget(db_path: str = DB_PATH) -> float:
    """
    Queries today's cumulative spend and raises BudgetExceededError if it
    meets or exceeds DAILY_BUDGET_CAP_USD.

    Returns:
        float: Current spend in USD (if under cap).

    Raises:
        BudgetExceededError: When the cap is reached or exceeded.
    """
    spent = get_today_spend(db_path)
    if spent >= DAILY_BUDGET_CAP_USD:
        logger.warning(
            f"cost_tracker: DAILY BUDGET CAP HIT — ${spent:.4f} >= ${DAILY_BUDGET_CAP_USD:.2f}. "
            "Halting cloud API calls."
        )
        raise BudgetExceededError(spent=spent, cap=DAILY_BUDGET_CAP_USD)
    return spent


def get_usage_summary(days: int = 7, db_path: str = DB_PATH) -> list[dict]:
    """
    Returns a list of daily cost summaries for the last `days` days,
    grouped by date and provider.

    Useful for exposing a `/api/cost-summary` endpoint.
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """
            SELECT date, provider,
                   SUM(input_tokens)        AS total_input,
                   SUM(output_tokens)       AS total_output,
                   SUM(calculated_cost_usd) AS total_cost
            FROM api_usage
            WHERE date >= date('now', ?)
            GROUP BY date, provider
            ORDER BY date DESC, provider
            """,
            (f"-{days} days",),
        ).fetchall()
        conn.close()
        return [
            {
                "date":           r[0],
                "provider":       r[1],
                "total_input_tk": r[2],
                "total_output_tk":r[3],
                "total_cost_usd": round(r[4], 6),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"cost_tracker: failed to query usage summary — {e}")
        return []
