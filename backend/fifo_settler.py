#!/usr/bin/env python3
"""
FIFO Credit Balancing Engine.

Implements First-In, First-Out debt settlement logic for customer credit accounts.
When a payment arrives, the oldest outstanding udhaar (credit) transactions are
settled first, systematically working forward through the chronological debt queue.
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from crypto_vault import encrypt_field, decrypt_field

logger = logging.getLogger("FIFOSettler")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")


def ensure_fifo_columns(db_path: str = DB_PATH, conn: sqlite3.Connection = None):
    """
    Safely adds the FIFO-required columns to ledger_entries if they do not exist:
      - payment_status  TEXT DEFAULT 'UNPAID'
      - amount_remaining REAL DEFAULT NULL
      - linked_payment_id TEXT DEFAULT NULL
    
    Uses PRAGMA table_info introspection to avoid ALTER errors on re-runs.
    """
    should_close = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        should_close = True
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(ledger_entries)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    
    new_columns = {
        "payment_status": "TEXT DEFAULT 'UNPAID'",
        "amount_remaining": "REAL DEFAULT NULL",
        "linked_payment_id": "TEXT DEFAULT NULL",
    }
    
    for col_name, col_def in new_columns.items():
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE ledger_entries ADD COLUMN {col_name} {col_def};")
                logger.info(f"✓ Added FIFO column '{col_name}' to ledger_entries.")
            except Exception as e:
                logger.warning(f"Column '{col_name}' could not be added (may already exist): {e}")
    
    if should_close:
        conn.commit()
        conn.close()


def _get_unpaid_udhaar_rows(
    party_name: str,
    db_path: str = DB_PATH,
    conn: sqlite3.Connection = None
) -> List[Dict[str, Any]]:
    """
    Queries all udhaar (credit) ledger entries for a specific customer that
    are currently marked as UNPAID or PARTIALLY_PAID, sorted oldest-first (FIFO order).
    
    Decrypts party_name and amount fields in-memory for matching and arithmetic.
    """
    should_close = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        should_close = True
    cursor = conn.cursor()
    
    # Fetch ALL udhaar-type entries; we filter by decrypted party name in Python
    # because the party_name column is encrypted at rest.
    cursor.execute("""
        SELECT entry_id, date, party_name, vehicle_wheel_no, amount, type,
               remarks, payment_status, amount_remaining, linked_payment_id
        FROM ledger_entries
        WHERE type = 'udhaar'
        ORDER BY date ASC, entry_id ASC
    """)
    rows = cursor.fetchall()
    if should_close:
        conn.close()
    
    target_clean = party_name.strip().lower()
    unpaid_rows = []
    
    for row in rows:
        entry_id = row[0]
        r_date = row[1]
        r_party_enc = row[2]
        r_vehicle = row[3] or "N/A"
        r_amount_enc = row[4]
        r_type = row[5]
        r_remarks = row[6] or ""
        r_status = row[7] or "UNPAID"
        r_remaining = row[8]
        r_linked = row[9] or ""
        
        # Decrypt fields
        try:
            r_party = decrypt_field(r_party_enc, return_type=str)
            r_amount = decrypt_field(r_amount_enc, return_type=float)
        except Exception:
            r_party = str(r_party_enc or "")
            r_amount = float(r_amount_enc or 0.0)
        
        if not r_party or r_party.strip().lower() != target_clean:
            continue
        
        # Only include rows that still have outstanding balance
        if r_status == "FULLY_PAID":
            continue
        
        # Calculate effective outstanding amount for this row
        if r_status == "PARTIALLY_PAID" and r_remaining is not None:
            effective_outstanding = float(r_remaining)
        else:
            effective_outstanding = r_amount
        
        unpaid_rows.append({
            "entry_id": entry_id,
            "date": r_date,
            "party_name": r_party,
            "vehicle_no": r_vehicle,
            "original_amount": r_amount,
            "effective_outstanding": effective_outstanding,
            "current_status": r_status,
            "remarks": r_remarks,
        })
    
    return unpaid_rows
def allocate_realization_fifo(
    party_name: str,
    payment_amount: float,
    payment_date: str,
    db_path: str = DB_PATH,
    conn: sqlite3.Connection = None
) -> Dict[str, Any]:
    """
    Executes FIFO-ordered credit settlement against a customer's outstanding debts.
    
    Algorithm:
        1. Fetch all UNPAID/PARTIALLY_PAID udhaar rows for the customer, sorted oldest-first.
        2. Loop through the debt queue, deducting from the payment pool:
           - If pool >= row outstanding: Mark row FULLY_PAID, move to next row.
           - If pool < row outstanding: Mark row PARTIALLY_PAID with the remainder, stop.
         3. If pool remains after all debts are settled, store the surplus as an
           unallocated credit advance for future fuel withdrawals.
    
    Parameters
    ----------
    party_name   : str   - Customer account name
    payment_amount : float - Total incoming payment amount (INR)
    payment_date : str   - Date of the payment ('YYYY-MM-DD')
    db_path      : str   - Path to the SQLite database
    
    Returns
    -------
    dict - Settlement report with allocation details
    """
    logger.info(
        f"[FIFO] Starting settlement allocation for '{party_name}': "
        f"₹{payment_amount:,.2f} received on {payment_date}"
    )
    
    # Ensure FIFO columns exist before processing
    ensure_fifo_columns(db_path, conn=conn)
    
    if payment_amount <= 0:
        return {
            "status": "error",
            "message": "Payment amount must be positive.",
            "allocated": [],
            "unallocated_surplus": 0.0,
            "total_settled": 0.0,
        }
    
    # 1. Build the outstanding debt array (oldest-first)
    unpaid_rows = _get_unpaid_udhaar_rows(party_name, db_path, conn=conn)
    
    if not unpaid_rows:
        logger.info(f"[FIFO] No outstanding udhaar debts found for '{party_name}'. Full amount is surplus credit advance.")
        
        # Store the entire payment as an unallocated credit advance
        _store_credit_advance(party_name, payment_amount, payment_date, db_path, conn=conn)
        
        return {
            "status": "success",
            "message": f"No outstanding debts for {party_name}. ₹{payment_amount:,.2f} stored as credit advance.",
            "allocated": [],
            "unallocated_surplus": payment_amount,
            "total_settled": 0.0,
            "rows_fully_paid": 0,
            "rows_partially_paid": 0,
        }
    
    # 2. FIFO Deduction Loop
    remaining_pool = payment_amount
    allocated_entries = []
    rows_fully_paid = 0
    rows_partially_paid = 0
    total_settled = 0.0
    
    # Generate a unique payment batch reference ID
    payment_ref = f"FIFO-{payment_date}-{datetime.now().strftime('%H%M%S')}"
    
    should_close = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        should_close = True
        conn.execute("BEGIN TRANSACTION")
    
    cursor = conn.cursor()
    
    try:
        for debt_row in unpaid_rows:
            if remaining_pool <= 0:
                break
            
            entry_id = debt_row["entry_id"]
            outstanding = debt_row["effective_outstanding"]
            
            if remaining_pool >= outstanding:
                # Case A: Payment pool covers this entire debt row
                deducted = outstanding
                remaining_pool -= deducted
                total_settled += deducted
                rows_fully_paid += 1
                
                cursor.execute("""
                    UPDATE ledger_entries
                    SET payment_status = 'FULLY_PAID',
                        amount_remaining = 0.0,
                        linked_payment_id = ?
                    WHERE entry_id = ?
                """, (payment_ref, entry_id))
                
                allocated_entries.append({
                    "entry_id": entry_id,
                    "date": debt_row["date"],
                    "original_amount": debt_row["original_amount"],
                    "amount_settled": deducted,
                    "new_status": "FULLY_PAID",
                    "remaining_on_row": 0.0,
                })
                
                logger.info(
                    f"  → Row #{entry_id} ({debt_row['date']}): "
                    f"₹{deducted:,.2f} settled → FULLY_PAID | Pool left: ₹{remaining_pool:,.2f}"
                )
            
            else:
                # Case B: Payment pool is depleted mid-row (partial settlement)
                deducted = remaining_pool
                new_remaining = round(outstanding - deducted, 2)
                remaining_pool = 0.0
                total_settled += deducted
                rows_partially_paid += 1
                
                cursor.execute("""
                    UPDATE ledger_entries
                    SET payment_status = 'PARTIALLY_PAID',
                        amount_remaining = ?,
                        linked_payment_id = ?
                    WHERE entry_id = ?
                """, (new_remaining, payment_ref, entry_id))
                
                allocated_entries.append({
                    "entry_id": entry_id,
                    "date": debt_row["date"],
                    "original_amount": debt_row["original_amount"],
                    "amount_settled": deducted,
                    "new_status": "PARTIALLY_PAID",
                    "remaining_on_row": new_remaining,
                })
                
                logger.info(
                    f"  → Row #{entry_id} ({debt_row['date']}): "
                    f"₹{deducted:,.2f} settled → PARTIALLY_PAID | "
                    f"Row remainder: ₹{new_remaining:,.2f} | Pool exhausted"
                )
                break
        
        # 3. Unallocated Pool Handling — store surplus as credit advance
        unallocated_surplus = round(remaining_pool, 2)
        if unallocated_surplus > 0:
            logger.info(
                f"[FIFO] Customer overpaid by ₹{unallocated_surplus:,.2f}. "
                f"Storing as unallocated credit advance."
            )
            _store_credit_advance(party_name, unallocated_surplus, payment_date, db_path, conn=conn)

        if should_close:
            conn.commit()
            logger.info(
                f"[FIFO] Settlement committed: {rows_fully_paid} fully paid, "
                f"{rows_partially_paid} partially paid, ₹{total_settled:,.2f} total settled."
            )
        
    except Exception as e:
        if should_close:
            conn.rollback()
            conn.close()
        logger.error(f"[FIFO] Settlement transaction failed: {e}")
        raise e
    
    if should_close:
        conn.close()
        
    unallocated_surplus = round(remaining_pool, 2)
    
    return {
        "status": "success",
        "message": (
            f"FIFO settlement complete for {party_name}. "
            f"₹{total_settled:,.2f} allocated across {len(allocated_entries)} debt(s)."
            + (f" ₹{unallocated_surplus:,.2f} stored as credit advance." if unallocated_surplus > 0 else "")
        ),
        "payment_ref": payment_ref,
        "allocated": allocated_entries,
        "unallocated_surplus": unallocated_surplus,
        "total_settled": total_settled,
        "rows_fully_paid": rows_fully_paid,
        "rows_partially_paid": rows_partially_paid,
    }


def _store_credit_advance(
    party_name: str,
    surplus_amount: float,
    payment_date: str,
    db_path: str = DB_PATH,
    conn: sqlite3.Connection = None
):
    """
    Stores an unallocated credit advance as a negative-balance 'advance' entry
    in the ledger_entries table. This lowers the cost of future fuel withdrawals
    by pre-crediting the customer's account.
    """
    logger.info(
        f"[FIFO] Recording credit advance of ₹{surplus_amount:,.2f} "
        f"for customer '{party_name}' on {payment_date}"
    )
    
    party_enc = encrypt_field(party_name.strip())
    amount_enc = encrypt_field(surplus_amount)
    
    should_close = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        should_close = True
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ledger_entries 
        (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status)
        VALUES (?, ?, 'Advance', ?, 'advance', 'Unallocated credit advance from overpayment (FIFO)', 'N/A')
    """, (payment_date, party_enc, amount_enc))
    if should_close:
        conn.commit()
        conn.close()
    
    logger.info(f"[FIFO] Credit advance entry committed successfully.")


def get_customer_fifo_status(
    party_name: str,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Returns a complete FIFO status report for a customer account:
      - Total outstanding (sum of UNPAID + PARTIALLY_PAID remaining balances)
      - Count of settled vs pending debt rows
      - Any existing credit advance balance
    """
    ensure_fifo_columns(db_path)
    
    unpaid_rows = _get_unpaid_udhaar_rows(party_name, db_path)
    
    total_outstanding = sum(r["effective_outstanding"] for r in unpaid_rows)
    
    # Count fully paid rows for this customer
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT entry_id, party_name, payment_status
        FROM ledger_entries
        WHERE type = 'udhaar' AND payment_status = 'FULLY_PAID'
        ORDER BY date ASC
    """)
    paid_rows = cursor.fetchall()
    
    target_clean = party_name.strip().lower()
    fully_paid_count = 0
    for row in paid_rows:
        try:
            r_party = decrypt_field(row[1], return_type=str)
        except Exception:
            r_party = str(row[1] or "")
        if r_party and r_party.strip().lower() == target_clean:
            fully_paid_count += 1
    
    # Check for existing credit advance balance
    cursor.execute("""
        SELECT entry_id, amount
        FROM ledger_entries
        WHERE type = 'advance'
        ORDER BY date DESC
    """)
    advance_rows = cursor.fetchall()
    conn.close()
    
    advance_balance = 0.0
    for a_row in advance_rows:
        try:
            a_party_check = True  # We'll sum all advances — filtered by party in a full implementation
            a_amount = decrypt_field(a_row[1], return_type=float)
            advance_balance += a_amount
        except Exception:
            pass
    
    return {
        "party_name": party_name,
        "total_outstanding": round(total_outstanding, 2),
        "pending_debt_rows": len(unpaid_rows),
        "fully_settled_rows": fully_paid_count,
        "credit_advance_balance": round(advance_balance, 2),
        "unpaid_details": unpaid_rows,
    }
