import os
import sqlite3
import logging
from typing import Any, Dict, List
import pandas as pd

logger = logging.getLogger("OMCReconciler")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_omc_reconciler_db(db_path: str = DB_PATH) -> None:
    """
    Initializes the omc_advance_ledger table in SQLite.
    """
    logger.info(f"Initializing omc_advance_ledger in {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS omc_advance_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            reference_no TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL, -- 'ADVANCE_DEPOSIT' or 'INVOICE_DEDUCTION'
            debit_amount REAL DEFAULT 0.0,  -- Invoices (reduces balance)
            credit_amount REAL DEFAULT 0.0, -- Deposits (increases balance)
            running_advance_balance REAL DEFAULT 0.0
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_omc_date ON omc_advance_ledger (transaction_date)
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'omc_advance_ledger' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'omc_advance_ledger' table: {str(e)}")
        raise e

def rebuild_omc_running_balances(db_path: str = DB_PATH) -> None:
    """
    Loads all transactions, sorts chronologically by date and id,
    and updates the running_advance_balance column sequentially.
    Balance formula: balance = previous_balance - debit_amount + credit_amount
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, debit_amount, credit_amount 
            FROM omc_advance_ledger 
            ORDER BY transaction_date ASC, id ASC
        """)
        rows = cursor.fetchall()
        
        running_balance = 0.0
        updates = []
        for row_id, debit, credit in rows:
            running_balance = round(running_balance - float(debit or 0.0) + float(credit or 0.0), 2)
            updates.append((running_balance, row_id))
            
        for new_bal, row_id in updates:
            cursor.execute("""
                UPDATE omc_advance_ledger 
                SET running_advance_balance = ? 
                WHERE id = ?
            """, (new_bal, row_id))
            
        conn.commit()
        conn.close()
        logger.info("OMC running balances rebuilt successfully.")
    except Exception as e:
        logger.error(f"Failed to rebuild OMC running balances: {str(e)}")

def log_omc_transaction(
    db_path: str,
    date_str: str,
    reference_no: str,
    description: str,
    debit: float,
    credit: float
) -> bool:
    """
    Safely records a transaction in the advance ledger.
    Prevents double logging by checking uniqueness of reference_no.
    """
    if not reference_no or not reference_no.strip():
        logger.warning("Reference number missing. Cannot log OMC transaction.")
        return False
        
    ref = str(reference_no).strip()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Prevent duplicate entries by checking reference_no
        cursor.execute("SELECT id FROM omc_advance_ledger WHERE reference_no = ?", (ref,))
        if cursor.fetchone():
            conn.close()
            return False
            
        cursor.execute("""
            INSERT INTO omc_advance_ledger 
            (transaction_date, reference_no, description, debit_amount, credit_amount)
            VALUES (?, ?, ?, ?, ?)
        """, (date_str.strip(), ref, description.strip(), float(debit), float(credit)))
        
        conn.commit()
        conn.close()
        
        # Chronologically rebuild running balance totals
        rebuild_omc_running_balances(db_path)
        return True
    except Exception as e:
        logger.error(f"Failed to log OMC transaction: {str(e)}")
        if conn:
            conn.close()
        return False

def audit_omc_statement_mismatches(omc_portal_csv_path: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Compares the local omc_advance_ledger against a dealer portal CSV statement.
    Flags uncredited deposits, pricing overcharges, and missing invoices.
    """
    if not os.path.exists(omc_portal_csv_path):
        raise FileNotFoundError(f"Dealer portal statement CSV not found: {omc_portal_csv_path}")
        
    # 1. Parse portal CSV
    df = pd.read_csv(omc_portal_csv_path)
    
    col_map = {}
    for col in df.columns:
        c_low = str(col).lower().strip()
        if c_low in ["date", "transaction_date", "posting_date", "txn_date"]:
            col_map[col] = "date"
        elif c_low in ["reference_no", "reference", "ref_no", "ref", "document_no", "doc_no", "utr", "chalan_no"]:
            col_map[col] = "reference_no"
        elif c_low in ["debit_amount", "debit", "amount_debit", "invoice_amount", "invoice_val"]:
            col_map[col] = "debit_amount"
        elif c_low in ["credit_amount", "credit", "amount_credit", "deposit_amount", "deposit_val"]:
            col_map[col] = "credit_amount"
            
    df = df.rename(columns=col_map)
    
    if "date" not in df.columns:
        raise ValueError("Dealer portal CSV is missing date column.")
    if "reference_no" not in df.columns:
        raise ValueError("Dealer portal CSV is missing reference number column.")
        
    if "debit_amount" not in df.columns:
        df["debit_amount"] = 0.0
    if "credit_amount" not in df.columns:
        df["credit_amount"] = 0.0
        
    df["reference_no"] = df["reference_no"].astype(str).str.strip()
    df["debit_amount"] = df["debit_amount"].fillna(0.0).astype(float)
    df["credit_amount"] = df["credit_amount"].fillna(0.0).astype(float)
    
    # Map portal rows by reference for lookup
    portal_dict = {}
    for _, row in df.iterrows():
        ref = row["reference_no"]
        if ref:
            portal_dict[ref] = {
                "date": str(row["date"]),
                "debit_amount": float(row["debit_amount"]),
                "credit_amount": float(row["credit_amount"])
            }
            
    # 2. Get local ledger entries
    conn = sqlite3.connect(db_path)
    local_df = pd.read_sql_query("""
        SELECT transaction_date, reference_no, description, debit_amount, credit_amount, running_advance_balance
        FROM omc_advance_ledger
    """, conn)
    conn.close()
    
    local_df["reference_no"] = local_df["reference_no"].astype(str).str.strip()
    local_df["debit_amount"] = local_df["debit_amount"].astype(float)
    local_df["credit_amount"] = local_df["credit_amount"].astype(float)
    
    # 3. Perform matching
    uncredited_deposits = []
    pricing_overcharges = []
    missing_invoices = []
    
    # Match local deposits (credit_amount > 0) to portal statement
    local_deposits = local_df[local_df["credit_amount"] > 0]
    for _, l_row in local_deposits.iterrows():
        ref = l_row["reference_no"]
        if ref not in portal_dict:
            uncredited_deposits.append({
                "transaction_date": l_row["transaction_date"],
                "reference_no": ref,
                "amount": float(l_row["credit_amount"])
            })
            
    # Match local invoices (debit_amount > 0) to portal statement to check overcharges
    local_invoices = local_df[local_df["debit_amount"] > 0]
    for _, l_row in local_invoices.iterrows():
        ref = l_row["reference_no"]
        if ref in portal_dict:
            p_row = portal_dict[ref]
            portal_debit = float(p_row["debit_amount"])
            local_debit = float(l_row["debit_amount"])
            if portal_debit > local_debit:
                pricing_overcharges.append({
                    "transaction_date": l_row["transaction_date"],
                    "reference_no": ref,
                    "local_amount": local_debit,
                    "portal_amount": portal_debit,
                    "overcharge": round(portal_debit - local_debit, 2)
                })
                
    # Match portal invoices (debit_amount > 0) to local statement to check missing
    local_invoice_refs = set(local_invoices["reference_no"])
    for _, p_row in df[df["debit_amount"] > 0].iterrows():
        ref = p_row["reference_no"]
        if ref not in local_invoice_refs:
            missing_invoices.append({
                "transaction_date": p_row["date"],
                "reference_no": ref,
                "amount": float(p_row["debit_amount"])
            })
            
    return {
        "uncredited_deposits": uncredited_deposits,
        "pricing_overcharges": pricing_overcharges,
        "missing_invoices": missing_invoices,
        "summary": {
            "total_local_deposits": round(float(local_df["credit_amount"].sum()), 2),
            "total_portal_deposits": round(float(df["credit_amount"].sum()), 2),
            "total_local_invoices": round(float(local_df["debit_amount"].sum()), 2),
            "total_portal_invoices": round(float(df["debit_amount"].sum()), 2),
            "discrepancies_count": len(uncredited_deposits) + len(pricing_overcharges) + len(missing_invoices)
        }
    }
