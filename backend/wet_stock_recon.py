#!/usr/bin/env python3
"""
Daily Underground Tank Wet Stock Reconciliation and Ledger Accounting Module.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional
import pandas as pd

logger = logging.getLogger("WetStockRecon")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")


def get_opening_stock(tank_id: str, date_string: str, db_path: str = DB_PATH) -> float:
    """
    Retrieves the closing stock of the chronologically preceding entry in stock_recon.
    Falls back to the current date's opening stock if no previous entry exists.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    col_closing = "hsd_closing_dip_liters" if tank_id == "Tank_1_HSD" else "ms_closing_dip_liters"
    
    # Query for the previous date's closing volume
    cursor.execute(f"""
        SELECT {col_closing} FROM stock_recon 
        WHERE date < ? 
        ORDER BY date DESC LIMIT 1
    """, (date_string,))
    row = cursor.fetchone()
    
    if row and row[0] is not None:
        val = float(row[0])
        conn.close()
        return val
        
    # Fallback to current date's opening stock
    col_opening = "hsd_opening_dip_liters" if tank_id == "Tank_1_HSD" else "ms_opening_dip_liters"
    cursor.execute(f"""
        SELECT {col_opening} FROM stock_recon 
        WHERE date = ?
    """, (date_string,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
        
    return 0.0


def get_inbound_receipts(tank_id: str, date_string: str, db_path: str = DB_PATH) -> float:
    """
    Sums the net actual received volumes from tanker_receipts for that date and product.
    """
    prod = "HSD" if tank_id == "Tank_1_HSD" else "MS"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tanker_receipts'")
    if not cursor.fetchone():
        conn.close()
        return 0.0
        
    cursor.execute("""
        SELECT SUM(actual_received_volume_liters) 
        FROM tanker_receipts 
        WHERE date = ? AND product_type = ?
    """, (date_string, prod))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
    return 0.0


def get_meter_sales_volume(tank_id: str, date_string: str, db_path: str = DB_PATH) -> float:
    """
    Retrieves total volume sales for all nozzles tied to the specific tank from daily_summary.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    col = "total_hsd_liters" if tank_id == "Tank_1_HSD" else "total_ms_liters"
    
    cursor.execute(f"SELECT {col} FROM daily_summary WHERE date = ?", (date_string,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
    return 0.0


def get_evening_dip_mm(tank_id: str, date_string: str, db_path: str = DB_PATH) -> Optional[float]:
    """
    Fetches the evening dip millimeter reading from tank_dip_log.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tank_dip_log'")
    if not cursor.fetchone():
        conn.close()
        return None
        
    cursor.execute("""
        SELECT dip_mm FROM tank_dip_log 
        WHERE tank_id = ? AND reading_date = ?
    """, (tank_id, date_string))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
    return None


def reconcile_tank_wet_stock(date_string: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Performs daily wet stock reconciliation for each active fuel tank:
    Tank_1_HSD (HSD, limit 0.20%) and Tank_2_MS (MS, limit 0.60%).
    """
    logger.info(f"Reconciling tank wet stock on {date_string} using database {db_path}...")
    
    tanks = [
        {"tank_id": "Tank_1_HSD", "product_type": "HSD", "shrinkage_limit_pct": 0.0020},
        {"tank_id": "Tank_2_MS", "product_type": "MS", "shrinkage_limit_pct": 0.0060}
    ]
    
    results = []
    
    for tank in tanks:
        tank_id = tank["tank_id"]
        prod = tank["product_type"]
        limit_pct = tank["shrinkage_limit_pct"]
        
        # 1. Fetch opening volume
        opening_volume = get_opening_stock(tank_id, date_string, db_path)
        
        # 2. Fetch inbound receipts
        inbound_receipts = get_inbound_receipts(tank_id, date_string, db_path)
        
        # 3. Fetch meter sales volume
        meter_sales = get_meter_sales_volume(tank_id, date_string, db_path)
        
        # 4. Compute Expected Closing Volume
        expected_closing = opening_volume + inbound_receipts - meter_sales
        
        # 5. Fetch actual physical closing volume
        dip_mm = get_evening_dip_mm(tank_id, date_string, db_path)
        if dip_mm is not None:
            from tank_calibration import convert_dip_to_liters
            actual_closing = convert_dip_to_liters(tank_id, dip_mm, db_path=db_path)
        else:
            # Fallback to stock_recon closing liters directly
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            col = "hsd_closing_dip_liters" if tank_id == "Tank_1_HSD" else "ms_closing_dip_liters"
            cursor.execute(f"SELECT {col} FROM stock_recon WHERE date = ?", (date_string,))
            row = cursor.fetchone()
            conn.close()
            actual_closing = float(row[0]) if (row and row[0] is not None) else 0.0
            
        # 6. Calculate Variance
        variance = actual_closing - expected_closing
        
        # 7. Loss Threshold Classification
        loss = -variance if variance < 0 else 0.0
        threshold = meter_sales * limit_pct
        
        if loss > threshold:
            status = "Abnormal Product Leakage Alert"
            logger.warning(
                f"OPERATIONAL WARNING: Tank {tank_id} on date {date_string} has abnormal leakage! "
                f"Variance: {variance:+.2f} L, Sales: {meter_sales:.2f} L, "
                f"Threshold: {threshold:.2f} L ({(limit_pct * 100):.2f}% of sales)"
            )
        else:
            status = "Normal Handling Shrinkage"
            
        results.append({
            "date": date_string,
            "tank_id": tank_id,
            "product_type": prod,
            "opening_volume": round(opening_volume, 2),
            "inbound_receipts": round(inbound_receipts, 2),
            "meter_sales_volume": round(meter_sales, 2),
            "expected_closing_volume": round(expected_closing, 2),
            "evening_dip_mm": round(dip_mm, 2) if dip_mm is not None else None,
            "actual_closing_volume": round(actual_closing, 2),
            "variance": round(variance, 2),
            "shrinkage_limit_pct": limit_pct,
            "shrinkage_limit_liters": round(threshold, 2),
            "status": status
        })
        
    return results


def generate_reconciliation_report_data(db_path: str = DB_PATH) -> pd.DataFrame:
    """
    Loops over all dates in the stock_recon table, runs reconciliation for all active tanks,
    and returns a Pandas DataFrame.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_recon'")
    if not cursor.fetchone():
        conn.close()
        return pd.DataFrame()
        
    cursor.execute("SELECT DISTINCT date FROM stock_recon ORDER BY date DESC")
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    all_rows = []
    for d in dates:
        try:
            tank_results = reconcile_tank_wet_stock(d, db_path=db_path)
            all_rows.extend(tank_results)
        except Exception as e:
            logger.error(f"Error during wet stock reconciliation for date {d}: {e}")
            
    if not all_rows:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_rows)
    return df
