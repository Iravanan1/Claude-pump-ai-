#!/usr/bin/env python3
"""
Cash Denomination Ledger Calculator Module.
Manages physical currency denominations counts, calculated sums,
and comparisons against expected register cash sales.
"""

import os
import sqlite3
import logging

logger = logging.getLogger("CashDenominations")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_cash_denominations_db(db_path: str = DB_PATH):
    """
    Initializes the cash_denominations table inside the SQLite database.
    """
    logger.info(f"Initializing cash_denominations table in: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash_denominations (
            date TEXT PRIMARY KEY,
            notes_500 INTEGER DEFAULT 0,
            notes_200 INTEGER DEFAULT 0,
            notes_100 INTEGER DEFAULT 0,
            notes_50 INTEGER DEFAULT 0,
            notes_20 INTEGER DEFAULT 0,
            notes_10 INTEGER DEFAULT 0,
            coins_total REAL DEFAULT 0.0,
            calculated_physical_sum REAL DEFAULT 0.0,
            mismatch_vs_book_sales REAL DEFAULT 0.0
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'cash_denominations' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'cash_denominations' table: {str(e)}")
        raise e

def verify_cash_vault_balance(date_string: str, note_counts_dict: dict, db_path: str = DB_PATH) -> dict:
    """
    Computes physical cash drawer totals, fetches expected book cash sales balance
    from the daily register summary and ledger logs, and records discrepancy deltas.
    """
    init_cash_denominations_db(db_path)
    
    # 1. Aggregate physical cash values
    n500 = int(note_counts_dict.get("notes_500", 0) or 0)
    n200 = int(note_counts_dict.get("notes_200", 0) or 0)
    n100 = int(note_counts_dict.get("notes_100", 0) or 0)
    n50 = int(note_counts_dict.get("notes_50", 0) or 0)
    n20 = int(note_counts_dict.get("notes_20", 0) or 0)
    n10 = int(note_counts_dict.get("notes_10", 0) or 0)
    coins = float(note_counts_dict.get("coins_total", 0.0) or 0.0)
    
    calculated_physical_sum = (
        n500 * 500 +
        n200 * 200 +
        n100 * 100 +
        n50 * 50 +
        n20 * 20 +
        n10 * 10 +
        coins
    )
    
    # 2. QueryExpected Book Cash Sales Balance
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Query daily summary
    cursor.execute("""
        SELECT total_cash_calculated, total_credit_sales FROM daily_summary WHERE date = ?
    """, (date_string,))
    summary_row = cursor.fetchone()
    
    # Query daily ledger
    cursor.execute("""
        SELECT cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales FROM daily_ledger WHERE date = ?
    """, (date_string,))
    ledger_row = cursor.fetchone()
    conn.close()
    
    # Calculate book_cash expected (net cash after UPI, card, and Udhaar deductions)
    calculated_sales = summary_row[0] if summary_row else 0.0
    if ledger_row:
        upi = float(ledger_row[1] or 0.0)
        paytm = float(ledger_row[2] or 0.0)
        card = float(ledger_row[3] or 0.0)
        udhaar = float(ledger_row[4] or 0.0)
        book_cash = max(0.0, calculated_sales - upi - paytm - card - udhaar)
    else:
        # Fallback if ledger row is missing
        credit_sales = summary_row[1] if summary_row else 0.0
        book_cash = max(0.0, calculated_sales - credit_sales)
        
    mismatch = calculated_physical_sum - book_cash
    
    # 3. Transactionally commit to database
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO cash_denominations (
                date, notes_500, notes_200, notes_100, notes_50, notes_20, notes_10, coins_total, calculated_physical_sum, mismatch_vs_book_sales
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_string,
            n500,
            n200,
            n100,
            n50,
            n20,
            n10,
            coins,
            calculated_physical_sum,
            mismatch
        ))
        conn.commit()
        conn.close()
        logger.info(f"✓ Cash denominations saved successfully for date {date_string}.")
    except Exception as commit_err:
        logger.error(f"Failed to commit cash denominations bindings: {commit_err}")
        raise commit_err
        
    return {
        "status": "success",
        "date": date_string,
        "notes_500": n500,
        "notes_200": n200,
        "notes_100": n100,
        "notes_50": n50,
        "notes_20": n20,
        "notes_10": n10,
        "coins_total": coins,
        "calculated_physical_sum": calculated_physical_sum,
        "expected_book_sales": book_cash,
        "mismatch_vs_book_sales": mismatch
    }

def get_cash_denomination(date_string: str, db_path: str = DB_PATH) -> dict:
    """
    Retrieves the cash denomination breakdown and mismatch for a given date.
    Returns a dictionary of counts, or all 0 values if none exist.
    """
    init_cash_denominations_db(db_path)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT notes_500, notes_200, notes_100, notes_50, notes_20, notes_10, coins_total, calculated_physical_sum, mismatch_vs_book_sales
            FROM cash_denominations WHERE date = ?
        """, (date_string,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "date": date_string,
                "notes_500": row[0],
                "notes_200": row[1],
                "notes_100": row[2],
                "notes_50": row[3],
                "notes_20": row[4],
                "notes_10": row[5],
                "coins_total": row[6],
                "calculated_physical_sum": row[7],
                "mismatch_vs_book_sales": row[8]
            }
        else:
            return {
                "date": date_string,
                "notes_500": 0,
                "notes_200": 0,
                "notes_100": 0,
                "notes_50": 0,
                "notes_20": 0,
                "notes_10": 0,
                "coins_total": 0.0,
                "calculated_physical_sum": 0.0,
                "mismatch_vs_book_sales": 0.0
            }
    except Exception as e:
        logger.error(f"Failed to query cash denominations for date {date_string}: {str(e)}")
        return {
            "date": date_string,
            "notes_500": 0,
            "notes_200": 0,
            "notes_100": 0,
            "notes_50": 0,
            "notes_20": 0,
            "notes_10": 0,
            "coins_total": 0.0,
            "calculated_physical_sum": 0.0,
            "mismatch_vs_book_sales": 0.0
        }
