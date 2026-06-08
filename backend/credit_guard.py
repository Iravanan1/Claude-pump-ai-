"""
Credit Limit Monitoring & Overdraft Alert Module.
Manages customer credit configuration limits and computes running ledger balances
to trigger overdraft warnings.
"""

import os
import sqlite3
import logging
from typing import Optional, Dict
from logger import logger
import crypto_vault

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_credit_db(db_path=DB_PATH):
    """
    Initializes the credit_limits configuration table in the SQLite database.
    """
    logger.info(f"Initializing credit_limits table in SQLite database at {os.path.abspath(db_path)}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_limits (
            party_name TEXT UNIQUE,
            max_allowed_udhaar REAL DEFAULT 0.0,
            alert_threshold_percentage REAL DEFAULT 80.0
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'credit_limits' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'credit_limits' table: {str(e)}")
        raise e

def get_running_customer_balance(party_name: str, db_path=DB_PATH) -> float:
    """
    Calculates the cumulative outstanding balance for a given entity by scanning
    all historical debits and credits recorded across the ledger entries.
    """
    if not party_name:
        return 0.0
        
    target_clean = party_name.strip().lower()
    balance = 0.0
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Read all entries to transparently decrypt and aggregate balances
        cursor.execute("SELECT party_name, amount, type FROM ledger_entries")
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            raw_party = row[0]
            raw_amount = row[1]
            row_type = row[2]
            
            # Decrypt fields using crypto_vault
            decrypted_party = crypto_vault.decrypt_field(raw_party, return_type=str)
            decrypted_amount = crypto_vault.decrypt_field(raw_amount, return_type=float)
            
            if decrypted_party and decrypted_party.strip().lower() == target_clean:
                # Aggregate debits and credits
                if row_type == "udhaar":
                    balance += decrypted_amount
                elif row_type in ("payment", "deposit", "receipt"):
                    balance -= decrypted_amount
                elif decrypted_amount < 0:
                    balance += decrypted_amount
                    
        return balance
    except Exception as e:
        logger.error(f"Failed to calculate running customer balance for {party_name}: {str(e)}")
        return 0.0

def set_credit_limit(party_name: str, max_allowed: float, threshold_percentage: float = 80.0, db_path=DB_PATH):
    """
    Sets or updates the credit limits for a given customer.
    """
    logger.info(f"Setting credit limit for customer '{party_name}' to {max_allowed}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO credit_limits (party_name, max_allowed_udhaar, alert_threshold_percentage)
            VALUES (?, ?, ?)
        """, (party_name.strip(), max_allowed, threshold_percentage))
        conn.commit()
        conn.close()
        logger.info(f"Credit limit for '{party_name}' successfully configured.")
    except Exception as e:
        logger.error(f"Failed to configure credit limit for '{party_name}': {str(e)}")
        raise e

def get_credit_limit(party_name: str, db_path=DB_PATH) -> Optional[Dict]:
    """
    Fetches the configured credit limit for a customer.
    Supports case-insensitive matching.
    """
    if not party_name:
        return None
        
    target_clean = party_name.strip().lower()
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check both plain-text match and retrieve configuration
        cursor.execute("""
            SELECT party_name, max_allowed_udhaar, alert_threshold_percentage 
            FROM credit_limits
        """)
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            cfg_party = row[0]
            if cfg_party and cfg_party.strip().lower() == target_clean:
                return {
                    "party_name": cfg_party,
                    "max_allowed_udhaar": float(row[1] or 0.0),
                    "alert_threshold_percentage": float(row[2] or 80.0)
                }
        return None
    except Exception as e:
        logger.error(f"Failed to query credit limits for '{party_name}': {str(e)}")
        return None

def check_credit_limit(party_name: str, incoming_credit_amount: float, db_path=DB_PATH) -> Optional[str]:
    """
    Evaluates outstanding running balances against the defined safety cap.
    Returns a high-visibility alert string if the cap or safety window is exceeded.
    """
    limit_cfg = get_credit_limit(party_name, db_path=db_path)
    if not limit_cfg:
        return None
        
    max_allowed = limit_cfg["max_allowed_udhaar"]
    if max_allowed <= 0.0:
        return None
        
    current_balance = get_running_customer_balance(party_name, db_path=db_path)
    new_balance = current_balance + incoming_credit_amount
    
    # If the safety limit is exceeded
    if new_balance > max_allowed:
        return f"Warning: {party_name} balance has exceeded their credit cap"
        
    # Optional threshold warning check
    threshold_fraction = limit_cfg["alert_threshold_percentage"] / 100.0
    if new_balance >= threshold_fraction * max_allowed:
        return f"Warning: {party_name} balance has approached their credit threshold"
        
    return None

def set_credit_threshold(party_name: str, max_allowed_credit: float, hard_block_status: bool = False, db_path=DB_PATH):
    """
    Sets or updates the credit threshold for a given customer.
    """
    logger.info(f"Setting credit threshold for customer '{party_name}' to {max_allowed_credit} (hard block: {hard_block_status})...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO credit_thresholds (party_name, max_allowed_credit, hard_block_status)
            VALUES (?, ?, ?)
        """, (party_name.strip(), max_allowed_credit, 1 if hard_block_status else 0))
        conn.commit()
        conn.close()
        logger.info(f"Credit threshold for '{party_name}' successfully configured.")
    except Exception as e:
        logger.error(f"Failed to configure credit threshold for '{party_name}': {str(e)}")
        raise e

def get_credit_threshold(party_name: str, db_path=DB_PATH) -> Optional[dict]:
    """
    Fetches the credit threshold configuration for a given customer.
    Supports case-insensitive matching.
    """
    if not party_name:
        return None
    target_clean = party_name.strip().lower()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, max_allowed_credit, hard_block_status FROM credit_thresholds")
        rows = cursor.fetchall()
        conn.close()
        for row in rows:
            cfg_party = row[0]
            if cfg_party and cfg_party.strip().lower() == target_clean:
                return {
                    "party_name": cfg_party,
                    "max_allowed_credit": float(row[1]),
                    "hard_block_status": bool(row[2])
                }
        return None
    except Exception as e:
        logger.error(f"Failed to query credit threshold for '{party_name}': {str(e)}")
        return None

def verify_transaction_credit_safety(party_name: str, current_slip_amount: float, db_path=DB_PATH, conn=None) -> dict:
    """
    Evaluates customer's outstanding balance + incoming amount against the safety threshold
    and hard block settings configured in the credit_thresholds table.
    """
    if not party_name:
        return {
            "party_name": "",
            "current_unpaid_sum": 0.0,
            "combined_total": current_slip_amount,
            "max_allowed_credit": None,
            "hard_blocked": False,
            "breached": False,
            "credit_status": "OK"
        }

    # Calculate exact current sum of all active, unpaid credit transactions (type='udhaar' and payment_status!='FULLY_PAID')
    from fifo_settler import _get_unpaid_udhaar_rows
    try:
        unpaid_rows = _get_unpaid_udhaar_rows(party_name, db_path=db_path, conn=conn)
        current_unpaid_sum = sum(row["effective_outstanding"] for row in unpaid_rows)
    except Exception as e:
        logger.error(f"Failed to calculate unpaid credit sum: {str(e)}")
        current_unpaid_sum = 0.0

    combined_total = current_unpaid_sum + current_slip_amount

    # Fetch credit boundary configuration from credit_thresholds
    threshold_cfg = get_credit_threshold(party_name, db_path=db_path)

    if not threshold_cfg:
        return {
            "party_name": party_name,
            "current_unpaid_sum": current_unpaid_sum,
            "combined_total": combined_total,
            "max_allowed_credit": None,
            "hard_blocked": False,
            "breached": False,
            "credit_status": "OK"
        }

    max_allowed = threshold_cfg["max_allowed_credit"]
    hard_blocked = threshold_cfg["hard_block_status"]
    breached = combined_total > max_allowed

    credit_status = "OK"
    if hard_blocked or breached:
        credit_status = "THRESHOLD_BREACH_WARNING"

    return {
        "party_name": party_name,
        "current_unpaid_sum": current_unpaid_sum,
        "combined_total": combined_total,
        "max_allowed_credit": max_allowed,
        "hard_blocked": hard_blocked,
        "breached": breached,
        "credit_status": credit_status
    }
