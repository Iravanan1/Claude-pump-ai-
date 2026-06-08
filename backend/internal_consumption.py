#!/usr/bin/env python3
"""
Internal Fuel Asset and Generator Consumption Tracker Module.
Tracks fuel volume draws for station operations, maps their cost as station
operational expenses, and coordinates stock totalizer integrations.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any

from crypto_vault import encrypt_field
from price_registry import get_rates_for_date

logger = logging.getLogger("InternalConsumption")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_internal_consumption_db(db_path: str = DB_PATH):
    """
    Initializes the internal_consumption SQLite tracking table.
    """
    logger.info(f"Initializing internal_consumption table in: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS internal_consumption (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_type TEXT CHECK(product_type IN ('HSD', 'MS')),
            liters_drawn REAL DEFAULT 0.0,
            purpose_head TEXT,
            authorized_by TEXT
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'internal_consumption' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'internal_consumption' table: {str(e)}")
        raise e

def record_internal_consumption(
    date_str: str,
    product_type: str,
    liters_drawn: float,
    purpose_head: str,
    authorized_by: str,
    db_path: str = DB_PATH
) -> dict:
    """
    Records an internal fuel draw, queries active date rates, calculates financial cost,
    and automatically commits an expense ledger transaction to ledger_entries.
    """
    init_internal_consumption_db(db_path)
    
    if product_type not in ('HSD', 'MS'):
        raise ValueError("Invalid product_type. Must be 'HSD' or 'MS'.")
        
    liters_drawn = float(liters_drawn)
    if liters_drawn <= 0:
        raise ValueError("Liters drawn must be positive.")
        
    # 1. Fetch rates from price registry
    rates = get_rates_for_date(date_str)
    if rates:
        rate = float(rates["hsd_rate"] if product_type == "HSD" else rates["ms_rate"])
    else:
        # Fallback standard base rates
        rate = 94.27 if product_type == "HSD" else 106.31
        
    cost = liters_drawn * rate
    
    # 2. Commit transactionally
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("BEGIN TRANSACTION")
        
        # Save raw draw entry
        cursor.execute("""
            INSERT INTO internal_consumption (date, product_type, liters_drawn, purpose_head, authorized_by)
            VALUES (?, ?, ?, ?, ?)
        """, (date_str, product_type, liters_drawn, purpose_head, authorized_by))
        
        # Save corresponding double-entry ledger entry as Operational Station Expense
        party_name_head = f"Internal Fuel Consumption - {purpose_head}"
        remarks_detail = f"Internal draw of {liters_drawn}L {product_type} for {purpose_head}, auth by {authorized_by}"
        
        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, 'expense', ?)
        """, (
            date_str,
            encrypt_field(party_name_head),
            "N/A",
            encrypt_field(cost),
            remarks_detail
        ))
        
        conn.commit()
        logger.info(f"✓ Recorded internal consumption and Operational Expense of ₹{cost:.2f} for date {date_str}.")
        
    except Exception as err:
        conn.rollback()
        logger.error(f"Failed to save internal consumption transaction: {err}")
        conn.close()
        raise err
        
    conn.close()
    
    return {
        "status": "success",
        "date": date_str,
        "product_type": product_type,
        "liters_drawn": liters_drawn,
        "purpose_head": purpose_head,
        "authorized_by": authorized_by,
        "applied_rate": rate,
        "total_financial_cost": cost
    }

def get_daily_internal_consumption(date_str: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves all detailed internal consumption logs recorded on the target date.
    """
    init_internal_consumption_db(db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT entry_id, date, product_type, liters_drawn, purpose_head, authorized_by
        FROM internal_consumption WHERE date = ?
        ORDER BY entry_id ASC
    """, (date_str,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def get_monthly_cumulative_consumption(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Compiles monthly cumulative summaries grouped by Month and Product Type.
    """
    init_internal_consumption_db(db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            strftime('%Y-%m', date) AS month,
            product_type,
            SUM(liters_drawn) AS cumulative_liters_drawn,
            COUNT(*) AS total_transactions
        FROM internal_consumption
        GROUP BY month, product_type
        ORDER BY month DESC, product_type ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]
