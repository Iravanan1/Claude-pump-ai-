#!/usr/bin/env python3
"""
Automated Daily Gross Profit Calculation Engine
===============================================
Computes:
  1. Gross Fuel Spread (INR & USD)
  2. Variance Cost Adjustment (from inventory shortages)
  3. Realized Daily Gross Profit
Stores results inside the daily_summary table of the master database.
"""

import os
import sqlite3
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ProfitEngine")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

# Constant exchange rate for gross spread conversion to USD
INR_USD_EXCHANGE_RATE = 83.0

def _get_rates_for_date(date_str: str, conn: sqlite3.Connection) -> Optional[dict]:
    """
    Directly query the fuel_rates table using the current connection handle,
    ensuring correct isolation during unit tests.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fuel_rates'")
    if not cursor.fetchone():
        return None
        
    cursor.execute("PRAGMA table_info(fuel_rates)")
    columns = [c[1] for c in cursor.fetchall()]
    
    if "premium_hsd_rate" in columns and "premium_ms_rate" in columns:
        cursor.execute("""
            SELECT hsd_rate, ms_rate, premium_hsd_rate, premium_ms_rate 
            FROM fuel_rates WHERE date = ?
        """, (date_str,))
        row = cursor.fetchone()
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
        if row:
            return {
                "hsd_rate": float(row[0]),
                "ms_rate": float(row[1]),
                "premium_hsd_rate": None,
                "premium_ms_rate": None
            }
    return None


def calculate_daily_fuel_profit(
    date_string: str,
    db_path: str = DEFAULT_DB_PATH,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Margin Evaluation & Mathematical Accounting Logic.
    
    1. Fetches regular/premium volumes sold from daily_summary.
    2. Resolves selling prices (RSP) from fuel_rates.
    3. Resolves cost prices (CP) from purchase_cost_log.
    4. Resolves tank shortage variances.
    5. Computes Gross Fuel Spread, Variance Cost Adjustment, and Realized Profit.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        close_conn = True

    try:
        cursor = conn.cursor()
        
        # 1. Fetch sales volumes
        cursor.execute("PRAGMA table_info(daily_summary)")
        cols = [c[1] for c in cursor.fetchall()]
        
        if not cols:
            logger.warning(f"daily_summary table does not exist or has no columns.")
            return {
                "date": date_string,
                "gross_spread_inr": 0.0,
                "gross_spread_usd": 0.0,
                "variance_cost_adjustment": 0.0,
                "realized_profit": 0.0
            }
            
        target_cols = ["total_hsd_liters", "total_ms_liters"]
        extra_cols = [
            "total_regular_hsd_liters", "total_premium_hsd_liters",
            "total_regular_ms_liters", "total_premium_ms_liters"
        ]
        present_extras = [col for col in extra_cols if col in cols]
        query_cols = target_cols + present_extras
        
        cursor.execute(
            f"SELECT {', '.join(query_cols)} FROM daily_summary WHERE date = ?",
            (date_string,)
        )
        row_data = cursor.fetchone()
        
        row_dict = dict(zip(query_cols, row_data)) if row_data else {}
        
        reg_hsd = float(row_dict.get("total_regular_hsd_liters") or row_dict.get("total_hsd_liters") or 0.0)
        prem_hsd = float(row_dict.get("total_premium_hsd_liters") or 0.0)
        reg_ms = float(row_dict.get("total_regular_ms_liters") or row_dict.get("total_ms_liters") or 0.0)
        prem_ms = float(row_dict.get("total_premium_ms_liters") or 0.0)
        
        # 2. Fetch Retail Selling Price (RSP)
        from premium_products import resolve_variant_rate
        rates = _get_rates_for_date(date_string, conn)
        hsd_reg_rate = resolve_variant_rate(rates, "REGULAR_HSD")
        hsd_prem_rate = resolve_variant_rate(rates, "PREMIUM_HSD")
        ms_reg_rate = resolve_variant_rate(rates, "REGULAR_MS")
        ms_prem_rate = resolve_variant_rate(rates, "PREMIUM_MS")
        
        # 3. Fetch Purchase Cost Price (CP)
        from purchase_registry import get_effective_purchase_cost
        hsd_cp = get_effective_purchase_cost(date_string, "HSD", db_path=db_path)
        ms_cp = get_effective_purchase_cost(date_string, "MS", db_path=db_path)
        
        # Apply sensible default CP margins if not configured in registry
        if hsd_cp is None:
            hsd_cp = round(hsd_reg_rate - 3.00, 2)
            logger.warning(f"No purchase CP found for HSD. Using default (RSP - 3.00): {hsd_cp}")
        if ms_cp is None:
            ms_cp = round(ms_reg_rate - 4.00, 2)
            logger.warning(f"No purchase CP found for MS. Using default (RSP - 4.00): {ms_cp}")
            
        # 4. Fetch Shortage Variance from wet stock reconciliation
        from wet_stock_recon import reconcile_tank_wet_stock
        hsd_variance = 0.0
        ms_variance = 0.0
        try:
            recon_list = reconcile_tank_wet_stock(date_string, db_path=db_path)
            for item in recon_list:
                if item.get("product_type") == "HSD":
                    hsd_variance = item.get("variance") or 0.0
                elif item.get("product_type") == "MS":
                    ms_variance = item.get("variance") or 0.0
        except Exception as e:
            logger.warning(f"Failed to retrieve wet stock reconciliation variance for {date_string}: {str(e)}")
            
        hsd_shortage = -hsd_variance if hsd_variance < 0 else 0.0
        ms_shortage = -ms_variance if ms_variance < 0 else 0.0
        
        # 5. Compute Mathematical Accounting Variables
        
        # Gross Fuel Spread (INR) = Volume Sold * (Retail Price - Purchase Cost)
        hsd_spread = (reg_hsd * (hsd_reg_rate - hsd_cp)) + (prem_hsd * (hsd_prem_rate - hsd_cp))
        ms_spread = (reg_ms * (ms_reg_rate - ms_cp)) + (prem_ms * (ms_prem_rate - ms_cp))
        gross_spread_inr = hsd_spread + ms_spread
        
        # Gross Spread (USD)
        gross_spread_usd = round(gross_spread_inr / INR_USD_EXCHANGE_RATE, 2)
        
        # Variance Cost Adjustment (INR) = Inventory Shortage Variance Liters * Purchase Cost
        variance_cost_adjustment = (hsd_shortage * hsd_cp) + (ms_shortage * ms_cp)
        
        # Realized Daily Gross Profit = Gross Fuel Spread - Variance Cost Adjustment
        realized_profit = round(gross_spread_inr - variance_cost_adjustment, 2)
        
        return {
            "date": date_string,
            "gross_spread_inr": round(gross_spread_inr, 2),
            "gross_spread_usd": gross_spread_usd,
            "variance_cost_adjustment": round(variance_cost_adjustment, 2),
            "realized_profit": realized_profit
        }
        
    finally:
        if close_conn:
            conn.close()


def calculate_and_store_daily_profit(
    date_string: str,
    db_path: str = DEFAULT_DB_PATH,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Summary Storage Hook.
    Runs the daily profit calculation and persists the resulting metrics
    (gross_spread_usd, realized_profit) inside the daily_summary table.
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(db_path)
        close_conn = True

    try:
        cursor = conn.cursor()
        
        # Run calculations
        result = calculate_daily_fuel_profit(date_string, db_path=db_path, conn=conn)
        
        # Check if profit columns exist in daily_summary
        cursor.execute("PRAGMA table_info(daily_summary)")
        cols = [c[1] for c in cursor.fetchall()]
        
        if "gross_spread_usd" in cols and "realized_profit" in cols:
            cursor.execute("""
                UPDATE daily_summary
                SET gross_spread_usd = ?, realized_profit = ?
                WHERE date = ?
            """, (result["gross_spread_usd"], result["realized_profit"], date_string))
            
            if close_conn:
                conn.commit()
                
            logger.info(
                f"[Profit Engine] Saved metrics for {date_string}: "
                f"Gross Spread = ${result['gross_spread_usd']}, "
                f"Realized Profit = ₹{result['realized_profit']}"
            )
        else:
            logger.warning("[Profit Engine] Table columns missing. Run database migrations first.")
            
        return result
        
    finally:
        if close_conn:
            conn.close()
