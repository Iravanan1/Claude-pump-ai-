"""
Multi-day Data Validation and Continuity Interlock Checker.
Compares a sheet's opening totalizers with the preceding chronological day's closing totalizers.
"""

import os
import json
import sqlite3
from typing import Dict, Any, List, Optional
from logger import logger
from crypto_vault import decrypt_raw_data

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def verify_chronological_continuity(
    target_date_string: str, 
    current_nozzles: Optional[List[Dict[str, Any]]] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Finds the committed daily entry immediately preceding target_date_string (e.g. Day minus 1)
    and checks if the opening meter readings on target_date lock with yesterday's closing readings.
    
    If current_nozzles is not provided, queries daily_ledger for target_date_string first.
    """
    target_date = target_date_string.strip()
    logger.info(f"Running chronological interlock checker for date: {target_date}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Resolve current day's nozzles
        if not current_nozzles:
            cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = ?", (target_date,))
            current_row = cursor.fetchone()
            if current_row and current_row[0]:
                try:
                    decrypted_raw = decrypt_raw_data(json.loads(current_row[0]))
                    current_nozzles = decrypted_raw.get("nozzles", [])
                except Exception as parse_err:
                    logger.warning(f"Failed to parse active nozzles from database: {str(parse_err)}")
                    current_nozzles = []
            else:
                current_nozzles = []
                
        # 2. Query database for preceding day record (highest date < target_date)
        cursor.execute("""
            SELECT date, raw_data FROM daily_ledger 
            WHERE date < ? 
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        preceding_row = cursor.fetchone()
        conn.close()
        
        # 3. If no preceding record exists, return a clean status indicating start of chain
        if not preceding_row:
            logger.info(f"No preceding daily records found before date {target_date}.")
            return {
                "status": "no_preceding_record",
                "preceding_date": None,
                "preceding_image_url": None,
                "discrepancies": []
            }
            
        preceding_date = preceding_row[0]
        preceding_raw_str = preceding_row[1]
        
        preceding_nozzles = []
        preceding_image_url = None
        
        if preceding_raw_str:
            try:
                decrypted_prec = decrypt_raw_data(json.loads(preceding_raw_str))
                preceding_nozzles = decrypted_prec.get("nozzles", [])
                preceding_image_url = decrypted_prec.get("image_url")
            except Exception as prec_err:
                logger.warning(f"Failed to parse preceding nozzles from date {preceding_date}: {str(prec_err)}")
                
        # 4. Perform interlocking comparison
        discrepancies = []
        
        # Map preceding nozzles by name for instant lookups
        prec_map = {n.get("nozzle_name"): n for n in preceding_nozzles if n.get("nozzle_name")}
        
        for cur_n in current_nozzles:
            name = cur_n.get("nozzle_name")
            if not name:
                continue
                
            cur_open = float(cur_n.get("opening") or 0.0)
            
            # Look up corresponding preceding nozzle
            prec_n = prec_map.get(name)
            if not prec_n:
                # Fallback: try case-insensitive matching or basic prefix match
                for p_name, p_n in prec_map.items():
                    if name.lower() in p_name.lower() or p_name.lower() in name.lower():
                        prec_n = p_n
                        break
                        
            if prec_n:
                prec_close = float(prec_n.get("closing") or 0.0)
                variance = round(cur_open - prec_close, 2)
                
                # Check for meter gaps (anomaly threshold > 0.01)
                if abs(variance) > 0.01:
                    logger.warning(
                        f"Discrepancy flagged: Nozzle '{name}' opening ({cur_open}) "
                        f"differs from preceding closing ({prec_close}) by {variance} L."
                    )
                    discrepancies.append({
                        "nozzle_name": name,
                        "current_opening": cur_open,
                        "preceding_closing": prec_close,
                        "variance": variance
                    })
                    
        status = "discrepancy" if discrepancies else "ok"
        logger.info(f"Chronological checker completed for {target_date}. Status: {status}. Discrepancies: {len(discrepancies)}")
        
        return {
            "status": status,
            "preceding_date": preceding_date,
            "preceding_image_url": preceding_image_url,
            "discrepancies": discrepancies
        }
        
    except Exception as e:
        logger.error(f"Failed to verify chronological continuity for date {target_date_string}: {str(e)}")
        return {
            "status": "error",
            "preceding_date": None,
            "preceding_image_url": None,
            "discrepancies": [],
            "error_details": str(e)
        }
