"""
Permissible Evaporation Loss Evaluation Engine.

Hardcoded standard Indian Oil Company max evaporation allowance coefficients:
- MS_ALLOWANCE_RATE = 0.0060 (0.6% for Motor Spirit / Petrol)
- HSD_ALLOWANCE_RATE = 0.0020 (0.2% for High Speed Diesel)
"""

import os
import sqlite3
import logging
from typing import Dict, Any

logger = logging.getLogger("EvaporationHandler")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

MS_ALLOWANCE_RATE = 0.0060
HSD_ALLOWANCE_RATE = 0.0020

def calculate_evaporation_allowances(date_string: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Computes max permissible volume loss and tags normal/abnormal losses.
    
    1. Fetch the day's total totalizer meter sales from daily_summary table.
    2. Calculate the max permissible volume loss in liters.
    3. Cross-reference this threshold against the actual physical stock variance from reconciliation.py.
    4. Code and classify normal/abnormal discrepancies.
    """
    logger.info(f"Evaluating permissible evaporation loss for date: {date_string}")
    
    # Initialize zero outcomes
    result = {
        "date": date_string,
        "hsd_sales_liters": 0.0,
        "ms_sales_liters": 0.0,
        "hsd_permissible_evaporation_liters": 0.0,
        "ms_permissible_evaporation_liters": 0.0,
        "hsd_actual_variance_liters": 0.0,
        "ms_actual_variance_liters": 0.0,
        "hsd_actual_shortage_liters": 0.0,
        "ms_actual_shortage_liters": 0.0,
        "hsd_normal_evaporation_loss_liters": 0.0,
        "ms_normal_evaporation_loss_liters": 0.0,
        "hsd_abnormal_shortage_liters": 0.0,
        "ms_abnormal_shortage_liters": 0.0,
        "hsd_classification": "No Discrepancy",
        "ms_classification": "No Discrepancy"
    }
    
    try:
        # Step 1: Query daily_summary for HSD and MS sales
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_hsd_liters, total_ms_liters FROM daily_summary WHERE date = ?",
            (date_string,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            result["hsd_sales_liters"] = float(row[0] or 0.0)
            result["ms_sales_liters"] = float(row[1] or 0.0)
    except Exception as e:
        logger.error(f"Error fetching daily sales for evaporation calculations: {e}")
        
    # Step 2: Calculate max permissible volume loss in liters
    result["hsd_permissible_evaporation_liters"] = round(result["hsd_sales_liters"] * HSD_ALLOWANCE_RATE, 3)
    result["ms_permissible_evaporation_liters"] = round(result["ms_sales_liters"] * MS_ALLOWANCE_RATE, 3)
    
    # Step 3: Fetch actual physical stock variance from reconciliation.py
    try:
        from reconciliation import calculate_daily_variance
        recon_variance = calculate_daily_variance(date_string, db_path=db_path)
        result["hsd_actual_variance_liters"] = float(recon_variance.get("hsd_variance_liters") or 0.0)
        result["ms_actual_variance_liters"] = float(recon_variance.get("ms_variance_liters") or 0.0)
    except Exception as e:
        logger.warning(f"Could not calculate stock variance for evaporation cross-reference: {e}")
        
    # Shortage represents a negative stock variance (physical closing stock < expected book stock)
    result["hsd_actual_shortage_liters"] = round(max(0.0, -result["hsd_actual_variance_liters"]), 3)
    result["ms_actual_shortage_liters"] = round(max(0.0, -result["ms_actual_variance_liters"]), 3)
    
    # Step 4: Account Coding Classification
    # Diesel (HSD)
    hsd_shortage = result["hsd_actual_shortage_liters"]
    hsd_permissible = result["hsd_permissible_evaporation_liters"]
    if hsd_shortage == 0.0:
        result["hsd_normal_evaporation_loss_liters"] = 0.0
        result["hsd_abnormal_shortage_liters"] = 0.0
        result["hsd_classification"] = "No Shortage / Surplus"
    elif hsd_shortage <= hsd_permissible:
        result["hsd_normal_evaporation_loss_liters"] = hsd_shortage
        result["hsd_abnormal_shortage_liters"] = 0.0
        result["hsd_classification"] = "Normal Evaporation Loss (Tax Deductible)"
    else:
        result["hsd_normal_evaporation_loss_liters"] = hsd_permissible
        result["hsd_abnormal_shortage_liters"] = round(hsd_shortage - hsd_permissible, 3)
        result["hsd_classification"] = "Abnormal Operational Shortage"
        
    # Petrol (MS)
    ms_shortage = result["ms_actual_shortage_liters"]
    ms_permissible = result["ms_permissible_evaporation_liters"]
    if ms_shortage == 0.0:
        result["ms_normal_evaporation_loss_liters"] = 0.0
        result["ms_abnormal_shortage_liters"] = 0.0
        result["ms_classification"] = "No Shortage / Surplus"
    elif ms_shortage <= ms_permissible:
        result["ms_normal_evaporation_loss_liters"] = ms_shortage
        result["ms_abnormal_shortage_liters"] = 0.0
        result["ms_classification"] = "Normal Evaporation Loss (Tax Deductible)"
    else:
        result["ms_normal_evaporation_loss_liters"] = ms_permissible
        result["ms_abnormal_shortage_liters"] = round(ms_shortage - ms_permissible, 3)
        result["ms_classification"] = "Abnormal Operational Shortage"
        
    logger.info(
        f"Evaporation analysis for {date_string}: \n"
        f"  - HSD: Sales={result['hsd_sales_liters']}L, Permissible Loss={hsd_permissible}L, "
        f"Actual Shortage={hsd_shortage}L -> Class: {result['hsd_classification']}\n"
        f"  - MS : Sales={result['ms_sales_liters']}L, Permissible Loss={ms_permissible}L, "
        f"Actual Shortage={ms_shortage}L -> Class: {result['ms_classification']}"
    )
    
    return result
