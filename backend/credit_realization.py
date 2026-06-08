"""
Credit Account Payment Realization and Settlement Engine.

Handles recording customer payments (debt collections), mapping them to the central 
ledger as balance-reducing payment entries, and querying realization logs.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any

from crypto_vault import encrypt_field, decrypt_field

logger = logging.getLogger("CreditRealization")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")


def init_realization_db(db_path: str = DB_PATH):
    """
    Initializes the credit_realizations table in the SQLite database.
    """
    logger.info(f"Initializing credit_realizations table in SQLite database at {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_realizations (
            realization_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            party_name TEXT NOT NULL,
            amount_received TEXT NOT NULL,
            payment_mode TEXT NOT NULL, -- 'CASH', 'BANK_TRANSFER', 'UPI'
            bank_utr_or_remarks TEXT,
            linked_invoice_no TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'credit_realizations' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'credit_realizations' table: {e}")
        raise e


def save_credit_realization(
    date_str: str,
    party_name: str,
    amount_received: float,
    payment_mode: str,
    bank_utr_or_remarks: str = None,
    linked_invoice_no: str = None,
    db_path: str = DB_PATH,
    conn: sqlite3.Connection = None
) -> int:
    """
    Commissions a credit payment realization:
      1. Commits encrypted customer payment details inside the 'credit_realizations' table.
      2. Automatically injects a corresponding balance-lowering credit entry ('payment')
         into the 'ledger_entries' table under the same encrypted party name, ensuring
         downstream account aging and invoices dynamically reflect the adjustment.
    """
    logger.info(f"Committing credit payment realization of ₹{amount_received} for customer '{party_name}'...")
    try:
        # Enforce exact upper casing on payment mode
        mode_clean = str(payment_mode or 'CASH').strip().upper()
        if mode_clean not in ('CASH', 'BANK_TRANSFER', 'UPI'):
            mode_clean = 'CASH'

        party_clean = str(party_name or '').strip()
        amount_val = float(amount_received or 0.0)
        utr_clean = str(bank_utr_or_remarks or '').strip()
        inv_clean = str(linked_invoice_no or '').strip()

        # Encrypt sensitive values for database insertion
        party_enc = encrypt_field(party_clean)
        amount_enc = encrypt_field(amount_val)

        should_close = False
        if conn is None:
            conn = sqlite3.connect(db_path)
            should_close = True
        cursor = conn.cursor()

        # 1. Insert into credit_realizations configuration table
        cursor.execute("""
            INSERT INTO credit_realizations 
            (date, party_name, amount_received, payment_mode, bank_utr_or_remarks, linked_invoice_no)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (date_str, party_enc, amount_enc, mode_clean, utr_clean, inv_clean))
        realization_id = cursor.lastrowid

        # 2. Insert corresponding credit reduction ('payment') in general ledger_entries
        ledger_remarks = f"Payment realized via {mode_clean}"
        if utr_clean:
            ledger_remarks += f" - UTR: {utr_clean}"
        if inv_clean:
            ledger_remarks += f" (Inv #{inv_clean})"

        cursor.execute("""
            INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
            VALUES (?, ?, ?, ?, 'payment', ?)
        """, (date_str, party_enc, "Payment", amount_enc, ledger_remarks))

        if should_close:
            conn.commit()
            conn.close()
        logger.info(f"Credit realization successfully saved (ID: {realization_id}) and ledger adjusted.")
        return realization_id

    except Exception as e:
        logger.error(f"Failed to record credit payment realization: {e}")
        raise e


def get_all_realizations(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Queries, decrypts in-memory, and returns all credit realizations chronologically.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT realization_id, date, party_name, amount_received, payment_mode, bank_utr_or_remarks, linked_invoice_no 
            FROM credit_realizations 
            ORDER BY date DESC, realization_id DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        realizations = []
        for r in rows:
            r_id, r_date, r_party_enc, r_amt_enc, r_mode, r_remarks, r_inv = r
            
            try:
                party = decrypt_field(r_party_enc, return_type=str)
                amount = decrypt_field(r_amt_enc, return_type=float)
            except Exception:
                party = str(r_party_enc or "")
                amount = float(r_amt_enc or 0.0)

            realizations.append({
                "realization_id": r_id,
                "date": r_date,
                "party_name": party,
                "amount_received": amount,
                "payment_mode": r_mode,
                "bank_utr_or_remarks": r_remarks,
                "linked_invoice_no": r_inv
            })

        return realizations
    except Exception as e:
        logger.error(f"Failed to query credit realizations: {e}")
        return []
