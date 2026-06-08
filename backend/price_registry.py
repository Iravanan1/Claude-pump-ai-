import os
import sqlite3
import pandas as pd
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PriceRegistry")

# Get current file directory
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_rates_db():
    """
    Initializes the fuel_rates table in the SQLite database.
    """
    logger.info(f"Initializing fuel_rates table in SQLite database at {DB_PATH}...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fuel_rates (
            date TEXT PRIMARY KEY,
            hsd_rate REAL NOT NULL,
            ms_rate REAL NOT NULL,
            premium_hsd_rate REAL DEFAULT NULL,
            premium_ms_rate REAL DEFAULT NULL
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'fuel_rates' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'fuel_rates' table: {str(e)}")
        raise e

def get_rates_for_date(date_str: str) -> dict:
    """
    Queries the fuel_rates table for the HSD and MS rates on the specified date.
    Returns:
        dict: containing 'hsd_rate', 'ms_rate', 'premium_hsd_rate', and 'premium_ms_rate', or None if rates not found.
    """
    logger.info(f"Querying fuel rates for date: {date_str}...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if premium columns exist in database first to avoid operational errors
        cursor.execute("PRAGMA table_info(fuel_rates)")
        columns = [c[1] for c in cursor.fetchall()]
        
        if "premium_hsd_rate" in columns and "premium_ms_rate" in columns:
            cursor.execute("SELECT hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate FROM fuel_rates WHERE date = ?", (date_str,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "hsd_rate": float(row[0]),
                    "ms_rate": float(row[1]),
                    "premium_hsd_rate": float(row[2]) if row[2] is not None else None,
                    "premium_ms_rate": float(row[3]) if row[3] is not None else None
                }
        else:
            cursor.execute("SELECT hsd_rate, ms_rate FROM fuel_rates WHERE date = ?", (date_str,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "hsd_rate": float(row[0]),
                    "ms_rate": float(row[1]),
                    "premium_hsd_rate": None,
                    "premium_ms_rate": None
                }
        return None
    except Exception as e:
        logger.error(f"Error querying rates for date {date_str}: {str(e)}")
        return None

def import_rate_csv(csv_path: str):
    """
    Imports a CSV file containing date, hsd_rate, ms_rate columns into fuel_rates table.
    Expects columns: date (YYYY-MM-DD), hsd_rate (float), ms_rate (float)
    Optional columns: premium_hsd_rate (float), premium_ms_rate (float)
    """
    logger.info(f"Importing fuel rates from CSV: {csv_path}...")
    try:
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV file not found at path: {csv_path}")
            
        df = pd.read_csv(csv_path)
        # Validate columns
        required_cols = {"date", "hsd_rate", "ms_rate"}
        if not required_cols.issubset(df.columns):
            raise ValueError(f"CSV file must contain columns: {required_cols}")
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if premium columns exist in target table
        cursor.execute("PRAGMA table_info(fuel_rates)")
        columns = [c[1] for c in cursor.fetchall()]
        has_prem_hsd = "premium_hsd_rate" in columns
        has_prem_ms = "premium_ms_rate" in columns
        
        for _, row in df.iterrows():
            date_val = str(row["date"]).strip()
            hsd_val = float(row["hsd_rate"])
            ms_val = float(row["ms_rate"])
            
            p_hsd_val = float(row["premium_hsd_rate"]) if ("premium_hsd_rate" in df.columns and pd.notna(row["premium_hsd_rate"])) else None
            p_ms_val = float(row["premium_ms_rate"]) if ("premium_ms_rate" in df.columns and pd.notna(row["premium_ms_rate"])) else None
            
            if has_prem_hsd and has_prem_ms:
                cursor.execute("""
                    INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
                    VALUES (?, ?, ?, ?, ?)
                """, (date_val, hsd_val, ms_val, p_hsd_val, p_ms_val))
            else:
                cursor.execute("""
                    INSERT OR REPLACE INTO fuel_rates (date, hsd_rate, ms_rate)
                    VALUES (?, ?, ?)
                """, (date_val, hsd_val, ms_val))
            
        conn.commit()
        conn.close()
        logger.info(f"Successfully imported {len(df)} price registry entries from {csv_path}.")
    except Exception as e:
        logger.error(f"Failed to import CSV: {str(e)}")
        raise e
