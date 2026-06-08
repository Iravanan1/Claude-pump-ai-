#!/usr/bin/env python3
"""
Staff Cash Advance & Salary Deduction Registry.
Manages employee borrowings (CASH_ADVANCE, FUEL_DRAWN) and aggregates them monthly for payroll deductions.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("StaffLedger")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_staff_ledger_db(db_path: str = DB_PATH):
    """
    Initializes the staff_advances table.
    """
    logger.info(f"Initializing staff_advances table in {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_advances (
            advance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            employee_name TEXT,
            amount_drawn REAL,
            type TEXT CHECK(type IN ('CASH_ADVANCE', 'FUEL_DRAWN')),
            remarks TEXT,
            settlement_status TEXT DEFAULT 'PENDING_DEDUCTION' CHECK(settlement_status IN ('PENDING_DEDUCTION', 'SETTLED_FROM_SALARY'))
        )
    """)
    # Indexing on employee name and date for high-speed retrieval
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_staff_emp_date ON staff_advances (employee_name, date)")
    conn.commit()
    conn.close()

def record_staff_advance_with_cursor(
    cursor: sqlite3.Cursor,
    date: str,
    employee_name: str,
    amount_drawn: float,
    atype: str,
    remarks: str,
    settlement_status: str = "PENDING_DEDUCTION"
):
    """
    Records a staff advance using an active database cursor within a transaction.
    """
    if atype not in ('CASH_ADVANCE', 'FUEL_DRAWN'):
        raise ValueError("Invalid staff advance type. Must be 'CASH_ADVANCE' or 'FUEL_DRAWN'.")
        
    if amount_drawn <= 0:
        raise ValueError("Amount drawn must be greater than zero.")
        
    cursor.execute("""
        INSERT INTO staff_advances (date, employee_name, amount_drawn, type, remarks, settlement_status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date.strip(), employee_name.strip(), float(amount_drawn), atype, remarks.strip(), settlement_status))

def record_staff_advance(
    date: str,
    employee_name: str,
    amount_drawn: float,
    atype: str,
    remarks: str,
    settlement_status: str = "PENDING_DEDUCTION",
    db_path: str = DB_PATH
) -> int:
    """
    Records a staff advance and returns the generated advance ID.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        record_staff_advance_with_cursor(cursor, date, employee_name, amount_drawn, atype, remarks, settlement_status)
        conn.commit()
        adv_id = cursor.lastrowid
        return adv_id
    finally:
        conn.close()

def delete_daily_staff_advances(date_str: str, conn: sqlite3.Connection):
    """
    Clears all staff advances recorded on a specific date to prevent duplication on shift saves.
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM staff_advances WHERE date = ?", (date_str.strip(),))

def get_daily_staff_advances(date_str: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves all staff advances recorded on a specific date.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT advance_id, date, employee_name, amount_drawn, type, remarks, settlement_status
        FROM staff_advances
        WHERE date = ?
    """, (date_str.strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]

def settle_staff_advance(advance_id: int, new_status: str, db_path: str = DB_PATH):
    """
    Updates the settlement status of an advance (e.g., to 'SETTLED_FROM_SALARY').
    """
    if new_status not in ('PENDING_DEDUCTION', 'SETTLED_FROM_SALARY'):
        raise ValueError("Invalid settlement status. Must be 'PENDING_DEDUCTION' or 'SETTLED_FROM_SALARY'.")
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE staff_advances
        SET settlement_status = ?
        WHERE advance_id = ?
    """, (new_status, int(advance_id)))
    conn.commit()
    conn.close()

def generate_monthly_payroll_deductions(
    employee_name: str,
    target_month: str,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Queries all pending advances for a specific employee within a target month (format YYYY-MM)
    and outputs a structured payroll deduction receipt.
    """
    # Normalize month format to YYYY-MM
    target_month = target_month.strip()
    if len(target_month) != 7 or target_month[4] != '-':
        raise ValueError("Invalid month format. Please use 'YYYY-MM' format (e.g. '2026-05').")
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Matches case-insensitively or exact by trimming
    cursor.execute("""
        SELECT advance_id, date, employee_name, amount_drawn, type, remarks, settlement_status
        FROM staff_advances
        WHERE LOWER(employee_name) = LOWER(?)
          AND date LIKE ?
          AND settlement_status = 'PENDING_DEDUCTION'
        ORDER BY date ASC
    """, (employee_name.strip(), f"{target_month}%"))
    
    rows = cursor.fetchall()
    conn.close()
    
    advances_list = [dict(r) for r in rows]
    total_deductions = sum(r["amount_drawn"] for r in advances_list)
    
    import datetime
    generation_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return {
        "status": "success",
        "employee_name": employee_name.strip(),
        "target_month": target_month,
        "total_deduction_amount": round(total_deductions, 2),
        "deductions_count": len(advances_list),
        "advances": advances_list,
        "generated_at": generation_timestamp,
        "receipt_header": f"PUMPAI STAFF SALARY DEDUCTION STATEMENT - {target_month}"
    }
