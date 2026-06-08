#!/usr/bin/env python3
"""
Premium Fuel and Multi-Product Variant Configuration Handler.
Manages schema evolution and SKU mappings for premium and regular variants.
"""

import sqlite3
import logging
from typing import Dict, Any

logger = logging.getLogger("PremiumProducts")

def migrate_premium_product_columns(db_path: str):
    """
    Safely evolves SQLite database schemas to support premium product variants.
    Adds regular/premium volume columns to daily_summary, and premium rates to fuel_rates.
    """
    logger.info(f"Running premium products schema evolution for database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. Update daily_summary table
        cursor.execute("PRAGMA table_info(daily_summary)")
        summary_cols = [col[1] for col in cursor.fetchall()]
        
        cols_to_add_summary = {
            "total_regular_hsd_liters": "REAL DEFAULT 0.0",
            "total_premium_hsd_liters": "REAL DEFAULT 0.0",
            "total_regular_ms_liters": "REAL DEFAULT 0.0",
            "total_premium_ms_liters": "REAL DEFAULT 0.0"
        }
        
        for col_name, col_type in cols_to_add_summary.items():
            if col_name not in summary_cols:
                logger.info(f"Adding column '{col_name}' to table 'daily_summary'...")
                cursor.execute(f"ALTER TABLE daily_summary ADD COLUMN {col_name} {col_type};")
                
        # 2. Update fuel_rates table
        # Ensure table exists first in case it's called before initialization
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS fuel_rates (
            date TEXT PRIMARY KEY,
            hsd_rate REAL NOT NULL,
            ms_rate REAL NOT NULL
        )
        """)
        
        cursor.execute("PRAGMA table_info(fuel_rates)")
        rates_cols = [col[1] for col in cursor.fetchall()]
        
        cols_to_add_rates = {
            "premium_hsd_rate": "REAL DEFAULT NULL",
            "premium_ms_rate": "REAL DEFAULT NULL"
        }
        
        for col_name, col_type in cols_to_add_rates.items():
            if col_name not in rates_cols:
                logger.info(f"Adding column '{col_name}' to table 'fuel_rates'...")
                cursor.execute(f"ALTER TABLE fuel_rates ADD COLUMN {col_name} {col_type};")
                
        conn.commit()
        logger.info("Premium products database schema migration finished successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to migrate database for premium products: {str(e)}")
        raise e
    finally:
        conn.close()

def map_nozzle_brand_to_sku(brand_name: str) -> str:
    """
    Maps a nozzle name, shorthand brand, or description to the canonical variant SKU tag:
    REGULAR_HSD, PREMIUM_HSD, REGULAR_MS, or PREMIUM_MS.
    """
    clean_name = str(brand_name).strip().upper()
    
    # 1. Check for explicit premium indicators/brands
    if any(term in clean_name for term in ["XP95", "XP 95", "SPEED", "OCTANE 95", "95 OCTANE", "PREMIUM_MS", "PREMIUM MS"]):
        return "PREMIUM_MS"
    elif any(term in clean_name for term in ["XTRAGREEN", "XTRAMILE", "PREMIUM_HSD", "PREMIUM HSD", "PREMIUM DIESEL"]):
        return "PREMIUM_HSD"
        
    # 2. Generic premium fallback check
    if "PREMIUM" in clean_name:
        if any(term in clean_name for term in ["HSD", "DIESEL"]):
            return "PREMIUM_HSD"
        return "PREMIUM_MS"
        
    # 3. Regular variants checks
    if "HSD" in clean_name or "DIESEL" in clean_name:
        return "REGULAR_HSD"
    elif "MS" in clean_name or "PETROL" in clean_name:
        return "REGULAR_MS"
        
    return "REGULAR_MS"

def resolve_variant_rate(rates_dict: Dict[str, Any], fuel_type: str) -> float:
    """
    Resolves the exact date-specific variant pricing from rates_dict.
    Applies custom pricing delta margins if premium rate fields are NULL.
    """
    if not rates_dict:
        return 94.27 if "HSD" in fuel_type else 106.31
        
    ft = str(fuel_type).strip().upper()
    
    hsd_base = float(rates_dict.get("hsd_rate") or 94.27)
    ms_base = float(rates_dict.get("ms_rate") or 106.31)
    
    if ft == "REGULAR_HSD":
        return hsd_base
    elif ft == "PREMIUM_HSD":
        p_hsd = rates_dict.get("premium_hsd_rate")
        return float(p_hsd) if p_hsd is not None else round(hsd_base + 3.0, 2)
    elif ft == "REGULAR_MS":
        return ms_base
    elif ft == "PREMIUM_MS":
        p_ms = rates_dict.get("premium_ms_rate")
        return float(p_ms) if p_ms is not None else round(ms_base + 5.0, 2)
        
    # Legacy fallback
    if ft == "HSD":
        return hsd_base
    return ms_base
