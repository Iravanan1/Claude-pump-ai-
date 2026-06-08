#!/usr/bin/env python3
"""
Price history lookback indexer and data re-calculation utility.
"""

import os
import json
import sqlite3
import logging
from typing import Tuple, Optional

logger = logging.getLogger("PriceGapFiller")

# DB_PATH will be dynamically rebound by workspace_manager at runtime.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")


def resolve_missing_fuel_price(target_date: str, product_type: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Scans chronologically backwards through the pricing log table (or fuel_rates table) to locate
    the single closest preceding date entry where a valid price was verified for that specific product (MS/HSD).
    Returns a tuple (price, previous_date) or (None, None) if not found.
    """
    logger.info(f"Resolving missing fuel price for target_date={target_date}, product_type={product_type}...")
    
    # Map product type to db column
    pt = str(product_type).upper().strip()
    if pt in ("MS", "REGULAR_MS"):
        col = "ms_rate"
    elif pt in ("HSD", "REGULAR_HSD"):
        col = "hsd_rate"
    elif pt in ("PREMIUM_MS",):
        col = "premium_ms_rate"
    elif pt in ("PREMIUM_HSD",):
        col = "premium_hsd_rate"
    else:
        # Fallback guessing
        if "HSD" in pt or "DIESEL" in pt:
            col = "hsd_rate"
        else:
            col = "ms_rate"

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if pricing_log table exists, otherwise use fuel_rates
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pricing_log'")
        has_pricing_log = cursor.fetchone() is not None
        table_name = "pricing_log" if has_pricing_log else "fuel_rates"
        
        # Verify columns exist first in the chosen table to avoid OperationalError
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = [c[1] for c in cursor.fetchall()]
        
        if col not in cols:
            # Fallback to basic rates if premium column doesn't exist
            if "premium" in col:
                col = "hsd_rate" if "hsd" in col else "ms_rate"
            else:
                conn.close()
                logger.warning(f"Column {col} not found in {table_name}")
                return None, None

        query = f"""
            SELECT {col}, date FROM {table_name}
            WHERE date < ? AND {col} IS NOT NULL AND {col} > 0
            ORDER BY date DESC LIMIT 1
        """
        cursor.execute(query, (target_date,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] is not None:
            price = float(row[0])
            prev_date = str(row[1])
            logger.info(f"Resolved baseline fallback price {price} from previous date {prev_date} in {table_name}")
            return price, prev_date
            
        logger.warning(f"No preceding price found for product {product_type} before {target_date} in {table_name}")
        return None, None
    except Exception as e:
        logger.error(f"Error in resolve_missing_fuel_price: {str(e)}")
        return None, None


def recalculate_ledger_revenue_by_rate(target_date: str, product_type: str, new_correct_rate: float) -> bool:
    """
    Recalculates the nozzle sales cash revenue for the target date and product type.
    Updates the rates table, daily_ledger (decrypting and updating raw_data), daily_summary,
    and regenerates master Excel and CSV outputs.
    """
    logger.info(f"Recalculating ledger revenue for date={target_date}, product={product_type}, rate={new_correct_rate}...")
    
    # 1. Map product type to db column
    pt = str(product_type).upper().strip()
    if pt in ("MS", "REGULAR_MS"):
        col = "ms_rate"
    elif pt in ("HSD", "REGULAR_HSD"):
        col = "hsd_rate"
    elif pt in ("PREMIUM_MS",):
        col = "premium_ms_rate"
    elif pt in ("PREMIUM_HSD",):
        col = "premium_hsd_rate"
    else:
        if "HSD" in pt or "DIESEL" in pt:
            col = "hsd_rate"
        else:
            col = "ms_rate"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # A. Resolve correct table name
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pricing_log'")
        has_pricing_log = cursor.fetchone() is not None
        table_name = "pricing_log" if has_pricing_log else "fuel_rates"
        
        # B. Ensure table exists & columns exist
        cursor.execute(f"PRAGMA table_info({table_name})")
        rates_cols = [c[1] for c in cursor.fetchall()]
        
        if col in rates_cols:
            cursor.execute(f"SELECT 1 FROM {table_name} WHERE date = ?", (target_date,))
            row_exists = cursor.fetchone() is not None
            if row_exists:
                cursor.execute(f"UPDATE {table_name} SET {col} = ? WHERE date = ?", (new_correct_rate, target_date))
            else:
                # Insert a new record. Resolve other rates to prevent NULL violations.
                hsd_val = 94.27
                ms_val = 106.31
                p_hsd_val = None
                p_ms_val = None
                
                # Check preceding entries for other rates
                if col != "hsd_rate":
                    cursor.execute(f"SELECT hsd_rate FROM {table_name} WHERE date < ? AND hsd_rate IS NOT NULL ORDER BY date DESC LIMIT 1", (target_date,))
                    prev_hsd = cursor.fetchone()
                    if prev_hsd: hsd_val = float(prev_hsd[0])
                else:
                    hsd_val = new_correct_rate
                    
                if col != "ms_rate":
                    cursor.execute(f"SELECT ms_rate FROM {table_name} WHERE date < ? AND ms_rate IS NOT NULL ORDER BY date DESC LIMIT 1", (target_date,))
                    prev_ms = cursor.fetchone()
                    if prev_ms: ms_val = float(prev_ms[0])
                else:
                    ms_val = new_correct_rate
                
                if "premium_hsd_rate" in rates_cols:
                    if col == "premium_hsd_rate":
                        p_hsd_val = new_correct_rate
                    else:
                        cursor.execute(f"SELECT premium_hsd_rate FROM {table_name} WHERE date < ? AND premium_hsd_rate IS NOT NULL ORDER BY date DESC LIMIT 1", (target_date,))
                        prev_p_hsd = cursor.fetchone()
                        if prev_p_hsd: p_hsd_val = float(prev_p_hsd[0])
                        
                if "premium_ms_rate" in rates_cols:
                    if col == "premium_ms_rate":
                        p_ms_val = new_correct_rate
                    else:
                        cursor.execute(f"SELECT premium_ms_rate FROM {table_name} WHERE date < ? AND premium_ms_rate IS NOT NULL ORDER BY date DESC LIMIT 1", (target_date,))
                        prev_p_ms = cursor.fetchone()
                        if prev_p_ms: p_ms_val = float(prev_p_ms[0])

                if "premium_hsd_rate" in rates_cols and "premium_ms_rate" in rates_cols:
                    cursor.execute(f"""
                        INSERT OR REPLACE INTO {table_name} (date, hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate)
                        VALUES (?, ?, ?, ?, ?)
                    """, (target_date, hsd_val, ms_val, p_hsd_val, p_ms_val))
                else:
                    cursor.execute(f"""
                        INSERT OR REPLACE INTO {table_name} (date, hsd_rate, ms_rate)
                        VALUES (?, ?, ?)
                    """, (target_date, hsd_val, ms_val))

        # C. Retrieve and update daily_ledger raw_data
        from crypto_vault import decrypt_raw_data, encrypt_raw_data
        
        cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = ?", (target_date,))
        ledger_row = cursor.fetchone()
        
        if ledger_row:
            raw_data_str = ledger_row[0]
            encrypted_data = json.loads(raw_data_str)
            decrypted_data = decrypt_raw_data(encrypted_data)
            
            total_cash = 0.0
            updated_nozzles = []
            
            nozzles_list = decrypted_data.get("nozzles", [])
            for nozzle in nozzles_list:
                fuel_type_nz = str(nozzle.get("fuel_type") or "").upper().strip()
                # Determine match
                is_match = False
                if pt in ("MS", "REGULAR_MS"):
                    is_match = fuel_type_nz in ("MS", "REGULAR_MS")
                elif pt in ("HSD", "REGULAR_HSD"):
                    is_match = fuel_type_nz in ("HSD", "REGULAR_HSD")
                elif pt == "PREMIUM_MS":
                    is_match = fuel_type_nz == "PREMIUM_MS"
                elif pt == "PREMIUM_HSD":
                    is_match = fuel_type_nz == "PREMIUM_HSD"
                
                if is_match:
                    nozzle["rate"] = new_correct_rate
                    # Recalculate amount
                    liters = float(nozzle.get("net_sales_liters") or nozzle.get("sales_liters_calculated") or 0.0)
                    nozzle["amount_calculated"] = round(liters * new_correct_rate, 2)
                    
                total_cash += float(nozzle.get("amount_calculated") or 0.0)
                updated_nozzles.append(nozzle)
                
            decrypted_data["nozzles"] = updated_nozzles
            decrypted_data["total_amount_inr"] = round(total_cash, 2)
            
            # Recalculate cash_tender
            udhaar = float(decrypted_data.get("udhaar_sales") or 0.0)
            expenses = float(decrypted_data.get("expenses_amount") or 0.0)
            decrypted_data["cash_tender"] = max(0.0, round(total_cash - udhaar - expenses, 2))
            
            # Re-encrypt
            encrypted_updated = encrypt_raw_data(decrypted_data)
            updated_json_str = json.dumps(encrypted_updated, ensure_ascii=False)
            
            # Update daily_ledger record
            cursor.execute("""
                UPDATE daily_ledger SET
                    total_amount_inr = ?,
                    cash_tender = ?,
                    raw_data = ?
                WHERE date = ?
            """, (decrypted_data["total_amount_inr"], decrypted_data["cash_tender"], updated_json_str, target_date))
            
            # D. Update daily_summary record
            cursor.execute("""
                UPDATE daily_summary SET
                    total_cash_calculated = ?
                WHERE date = ?
            """, (decrypted_data["total_amount_inr"], target_date))
            
        conn.commit()
        conn.close()
        
        logger.info(f"SQLite tables updated successfully for date {target_date}.")
        
        # E. Regenerate Excel files
        try:
            from exporter import export_db_to_excel, generate_accounting_export
            import main
            
            # Resolve EXCEL_PATH using main or environment
            excel_path = getattr(main, "EXCEL_PATH", None)
            if not excel_path:
                excel_path = os.environ.get("EXPORT_EXCEL_PATH")
            if not excel_path:
                # Default fallback
                root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                excel_path = os.path.join(root_dir, "pump_exports", "Pump_Accounts.xlsx")
                
            export_db_to_excel(excel_path)
            generate_accounting_export(target_date)
            logger.info("Master Excel outputs updated successfully.")
        except Exception as excel_err:
            logger.error(f"Failed to update master Excel output during recalculation: {str(excel_err)}")
            
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to recalculate ledger revenue: {str(e)}")
        raise e
