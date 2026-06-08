"""
Delivery Salesman (DSM) Shift Allocation and Accounting Tracker.
Manages DSM assignments to nozzles, recorded cash/digital handovers,
and calculates individual shortages/excesses.
"""

import os
import sqlite3
from typing import List, Dict, Optional
from logger import logger

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_dsm_db(db_path: str = DB_PATH):
    """
    Initializes the dsm_shifts table in the SQLite database.
    """
    logger.info(f"Initializing dsm_shifts table in SQLite database at {os.path.abspath(db_path)}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS dsm_shifts (
            date TEXT,
            shift_type TEXT,
            dsm_name TEXT,
            assigned_nozzles TEXT,
            cash_handed_over REAL DEFAULT 0.0,
            digital_slips_value REAL DEFAULT 0.0,
            calculated_shortage_or_excess REAL DEFAULT 0.0,
            UNIQUE(date, shift_type, dsm_name)
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'dsm_shifts' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'dsm_shifts' table: {str(e)}")
        raise e

def save_dsm_shift(
    date_str: str,
    shift_type: str,
    dsm_name: str,
    assigned_nozzles: str,
    cash_handed_over: float,
    digital_slips_value: float = 0.0,
    calculated_shortage_or_excess: float = 0.0,
    db_path: str = DB_PATH
):
    """
    Saves or updates a DSM shift record in the SQLite database.
    """
    logger.info(f"Saving DSM shift: {dsm_name} on {date_str} ({shift_type})...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO dsm_shifts (
                date, shift_type, dsm_name, assigned_nozzles,
                cash_handed_over, digital_slips_value, calculated_shortage_or_excess
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str.strip(),
            shift_type.strip(),
            dsm_name.strip(),
            assigned_nozzles.strip(),
            float(cash_handed_over),
            float(digital_slips_value),
            float(calculated_shortage_or_excess)
        ))
        conn.commit()
        conn.close()
        logger.info(f"DSM shift for {dsm_name} saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save DSM shift: {str(e)}")
        raise e

def get_dsm_shifts_by_date(date_str: str, db_path: str = DB_PATH) -> List[Dict]:
    """
    Retrieves all DSM shifts recorded for a specific date.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT shift_type, dsm_name, assigned_nozzles, cash_handed_over, 
                   digital_slips_value, calculated_shortage_or_excess
            FROM dsm_shifts WHERE date = ?
        """, (date_str.strip(),))
        rows = cursor.fetchall()
        conn.close()
        
        shifts = []
        for r in rows:
            shifts.append({
                "date": date_str,
                "shift_type": r[0],
                "dsm_name": r[1],
                "assigned_nozzles": r[2],
                "cash_handed_over": float(r[3] or 0.0),
                "digital_slips_value": float(r[4] or 0.0),
                "calculated_shortage_or_excess": float(r[5] or 0.0)
            })
        return shifts
    except Exception as e:
        logger.error(f"Failed to fetch DSM shifts for date {date_str}: {str(e)}")
        return []

def get_all_dsm_shifts(db_path: str = DB_PATH) -> List[Dict]:
    """
    Retrieves all DSM shifts in chronological order.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, shift_type, dsm_name, assigned_nozzles, cash_handed_over, 
                   digital_slips_value, calculated_shortage_or_excess
            FROM dsm_shifts ORDER BY date ASC, shift_type ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        
        shifts = []
        for r in rows:
            shifts.append({
                "date": r[0],
                "shift_type": r[1],
                "dsm_name": r[2],
                "assigned_nozzles": r[3],
                "cash_handed_over": float(r[4] or 0.0),
                "digital_slips_value": float(r[5] or 0.0),
                "calculated_shortage_or_excess": float(r[6] or 0.0)
            })
        return shifts
    except Exception as e:
        logger.error(f"Failed to fetch all DSM shifts: {str(e)}")
        return []

def delete_dsm_shifts_by_date(date_str: str, db_path: str = DB_PATH):
    """
    Deletes all DSM shift allocations for a specific date (enables safe overrides).
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM dsm_shifts WHERE date = ?", (date_str.strip(),))
        conn.commit()
        conn.close()
        logger.info(f"Deleted dsm shifts for date {date_str}.")
    except Exception as e:
        logger.error(f"Failed to delete DSM shifts for date {date_str}: {str(e)}")
        raise e

def calculate_dsm_expected_sales(
    date_str: str,
    assigned_nozzles_list: List[str],
    db_path: str = DB_PATH
) -> float:
    """
    Computes expected sales amount for a set of nozzles on a specific date.
    Looks up decrypted nozzle records from daily_ledger raw_data.
    """
    if not assigned_nozzles_list:
        return 0.0
        
    cleaned_nozzles = [n.strip().lower() for n in assigned_nozzles_list if n.strip()]
    expected_sales = 0.0
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = ?", (date_str.strip(),))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return 0.0
            
        import json
        import crypto_vault
        raw_json_str = row[0]
        if raw_json_str:
            data = json.loads(raw_json_str)
            decrypted = crypto_vault.decrypt_raw_data(data)
            
            for nozzle in decrypted.get("nozzles", []):
                nozzle_name = nozzle.get("nozzle_name", "").strip().lower()
                # Matches if the assigned string is a substring (e.g. "MS-1" matches "MS-1 (Petrol)" or "ms-1")
                if any(cn in nozzle_name for cn in cleaned_nozzles):
                    # Prioritize amount_calculated or amount_transcribed
                    amt = float(nozzle.get("amount_calculated") or nozzle.get("amount_transcribed") or 0.0)
                    expected_sales += amt
                    
        return expected_sales
    except Exception as e:
        logger.error(f"Failed to calculate expected sales for nozzles {assigned_nozzles_list} on {date_str}: {str(e)}")
        return 0.0
