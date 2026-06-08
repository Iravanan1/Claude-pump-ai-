"""
Card Swipe Machine Reconciliation Module.
Manages schema expansion, automated MDR calculations, and bank credit projections.
"""

import os
import sqlite3
import logging
from typing import Dict, Any, List, Tuple

# Setup logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CardSettlements")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_card_settlements_db(db_path: str = DB_PATH):
    """
    Initializes the SQLite card_settlements table and index.
    """
    logger.info(f"Initializing card_settlements table in {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            machine_provider TEXT,
            gross_swipes_copied REAL DEFAULT 0.0,
            bank_charges_mdr REAL DEFAULT 0.0,
            expected_net_credit REAL DEFAULT 0.0,
            reconciliation_status TEXT DEFAULT 'Pending'
        )
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_card_settlements_date ON card_settlements (date)
        """)
        
        conn.commit()
        conn.close()
        logger.info("card_settlements table initialized successfully!")
    except Exception as e:
        logger.error(f"Failed to initialize card_settlements database: {str(e)}")
        raise e

def calculate_net_settlement(gross_amount: float, provider: str) -> Tuple[float, float]:
    """
    Applies MDR fee percentages to project the net bank deposit and charges:
    - RuPay / Debit Card machine mappings: 0% MDR
    - Commercial Card or HDFC POS machine mappings: 0.9% MDR
    - SBI Touch or other POS machine mappings: 0.75% MDR
    - Default/other credit cards: 1.0% MDR
    
    Returns a tuple: (bank_charges_mdr, expected_net_credit)
    """
    if not gross_amount:
        return 0.0, 0.0
        
    prov_lower = str(provider).strip().lower()
    
    if "rupay" in prov_lower or "debit" in prov_lower:
        rate = 0.0
    elif "hdfc" in prov_lower or "commercial" in prov_lower:
        rate = 0.009
    elif "sbi" in prov_lower:
        rate = 0.0075
    else:
        rate = 0.01 # 1.0% default
        
    bank_charges_mdr = round(gross_amount * rate, 2)
    expected_net_credit = round(gross_amount - bank_charges_mdr, 2)
    return bank_charges_mdr, expected_net_credit

def save_card_settlements(date_str: str, settlements: List[Dict[str, Any]], db_path: str = DB_PATH):
    """
    Saves a list of card swipe machine records for a given date, deleting prior entries.
    Each settlement dict should contain: 'machine_provider' and 'gross_swipes_copied'.
    """
    logger.info(f"Saving card settlements for date {date_str} in {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Clean existing settlements for this date to prevent duplicates
        cursor.execute("DELETE FROM card_settlements WHERE date = ?", (date_str,))
        
        # 2. Insert new settlements
        for item in settlements:
            provider = item.get("machine_provider") or "Unknown POS"
            gross = float(item.get("gross_swipes_copied") or 0.0)
            status = item.get("reconciliation_status") or "Pending"
            
            # Auto-calculate bank charges and expected net credits
            charges, net = calculate_net_settlement(gross, provider)
            
            cursor.execute("""
            INSERT INTO card_settlements (date, machine_provider, gross_swipes_copied, bank_charges_mdr, expected_net_credit, reconciliation_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (date_str, provider, gross, charges, net, status))
            
        conn.commit()
        conn.close()
        logger.info(f"Successfully committed {len(settlements)} card settlements.")
    except Exception as e:
        logger.error(f"Failed to save card settlements: {str(e)}")
        raise e

def get_card_settlements_by_date(date_str: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Pulls all machine POS swipe records for a specific date from SQLite.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT date, machine_provider, gross_swipes_copied, bank_charges_mdr, expected_net_credit, reconciliation_status
        FROM card_settlements
        WHERE date = ?
        ORDER BY id ASC
        """, (date_str,))
        
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            results.append({
                "date": r["date"],
                "machine_provider": r["machine_provider"],
                "gross_swipes_copied": r["gross_swipes_copied"],
                "bank_charges_mdr": r["bank_charges_mdr"],
                "expected_net_credit": r["expected_net_credit"],
                "reconciliation_status": r["reconciliation_status"]
            })
        return results
    except Exception as e:
        logger.error(f"Failed to query card settlements: {str(e)}")
        return []
