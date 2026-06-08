#!/usr/bin/env python3
"""
Nozzle Testing and Return-to-Stock (RTS) Allocation Module.
Tracks calibration nozzle tests, Return-to-Stock verification status,
and feeds adjusted volume and revenue calculations into core systems.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any

logger = logging.getLogger("TestingRTS")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_testing_rts_db(db_path: str = DB_PATH):
    """
    Initializes the nozzle_testing_logs SQLite tracking table idempotently.
    """
    logger.info(f"Initializing nozzle_testing_logs table in: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS nozzle_testing_logs (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            nozzle_id TEXT,
            product_type TEXT CHECK(product_type IN ('HSD', 'MS')),
            testing_volume_liters REAL DEFAULT 5.0,
            rts_verified INTEGER DEFAULT 1 -- 0 for False, 1 for True
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'nozzle_testing_logs' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'nozzle_testing_logs' table: {str(e)}")
        raise e

def record_nozzle_testing(
    date_str: str,
    nozzle_id: str,
    product_type: str,
    testing_volume_liters: float = 5.0,
    rts_verified: bool = True,
    db_path: str = DB_PATH
) -> dict:
    """
    Records a nozzle testing calibration event and marks the Return-to-Stock verification status.
    """
    init_testing_rts_db(db_path)
    
    if product_type not in ('HSD', 'MS'):
        raise ValueError("Invalid product_type. Must be 'HSD' or 'MS'.")
        
    testing_volume_liters = float(testing_volume_liters)
    if testing_volume_liters <= 0:
        raise ValueError("Testing volume must be positive.")
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("BEGIN TRANSACTION")
        
        cursor.execute("""
            INSERT INTO nozzle_testing_logs (date, nozzle_id, product_type, testing_volume_liters, rts_verified)
            VALUES (?, ?, ?, ?, ?)
        """, (date_str, nozzle_id, product_type, testing_volume_liters, 1 if rts_verified else 0))
        
        conn.commit()
        logger.info(f"✓ Recorded nozzle testing event: {testing_volume_liters}L of {product_type} on nozzle {nozzle_id} for date {date_str} (RTS: {rts_verified}).")
        
    except Exception as err:
        conn.rollback()
        logger.error(f"Failed to record nozzle testing event: {err}")
        conn.close()
        raise err
        
    conn.close()
    
    return {
        "status": "success",
        "date": date_str,
        "nozzle_id": nozzle_id,
        "product_type": product_type,
        "testing_volume_liters": testing_volume_liters,
        "rts_verified": rts_verified
    }

def get_daily_testing_logs(date_str: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves all detailed nozzle testing logs recorded on the specified date.
    """
    init_testing_rts_db(db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT entry_id, date, nozzle_id, product_type, testing_volume_liters, rts_verified
        FROM nozzle_testing_logs WHERE date = ?
        ORDER BY entry_id ASC
    """, (date_str,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]
