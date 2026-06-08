#!/usr/bin/env python3
"""
Fuel Purchase Cost Price (CP) Registry Module
=============================================
Manages fuel purchase cost rates (base cost + transportation + local taxes)
for HSD and MS products. Provides a flexible chronological lookup that falls
back to the most recent preceding rate when no exact date match exists.
"""

import os
import sqlite3
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("PurchaseRegistry")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def ensure_table(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Ensures that the purchase_cost_log table exists in the database.
    (Also handled by migration VERSION 7, but provides runtime safety).
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_cost_log (
            effective_date TEXT,
            product_type TEXT CHECK(product_type IN ('HSD', 'MS')),
            purchase_rate_per_liter REAL NOT NULL,
            invoice_reference TEXT,
            PRIMARY KEY (effective_date, product_type)
        )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to ensure purchase_cost_log table: {str(e)}")


def get_effective_purchase_cost(
    target_date: str,
    product_type: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[float]:
    """
    Flexible Date Lookup Logic.
    
    Scans the table chronologically to find the purchase rate active on that specific date.
    If no rate is entered on that exact day, it automatically falls back to the most
    recent preceding purchase rate recorded.
    
    Returns the rate (float) or None if no matching or preceding rate exists.
    """
    ensure_table(db_path)
    if product_type not in ("HSD", "MS"):
        logger.warning(f"Invalid product type requested for lookup: {product_type}")
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Scan chronologically for the most recent rate <= target_date
        cursor.execute("""
            SELECT purchase_rate_per_liter FROM purchase_cost_log
            WHERE product_type = ? AND effective_date <= ?
            ORDER BY effective_date DESC
            LIMIT 1
        """, (product_type, target_date))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return float(row[0])
            
        logger.warning(f"No purchase cost rate found on or before {target_date} for {product_type}.")
        return None
    except Exception as e:
        logger.error(f"Error looking up purchase cost rate for {product_type} on {target_date}: {str(e)}")
        return None


def get_all_purchase_rates(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves all purchase cost rate entries sorted by effective_date descending.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT effective_date, product_type, purchase_rate_per_liter, invoice_reference
            FROM purchase_cost_log
            ORDER BY effective_date DESC, product_type ASC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch purchase rates: {str(e)}")
        return []


def upsert_purchase_rate(
    effective_date: str,
    product_type: str,
    purchase_rate_per_liter: float,
    invoice_reference: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Inserts or replaces a purchase cost rate record.
    """
    ensure_table(db_path)
    if product_type not in ("HSD", "MS"):
        raise ValueError(f"Invalid product type: {product_type}. Must be 'HSD' or 'MS'.")
    if purchase_rate_per_liter <= 0:
        raise ValueError("Purchase rate must be a positive number.")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO purchase_cost_log
            (effective_date, product_type, purchase_rate_per_liter, invoice_reference)
            VALUES (?, ?, ?, ?)
        """, (effective_date.strip(), product_type, float(purchase_rate_per_liter), invoice_reference.strip()))
        
        conn.commit()
        conn.close()
        logger.info(f"Successfully saved purchase cost rate: {product_type} on {effective_date} = {purchase_rate_per_liter}")
    except Exception as e:
        logger.error(f"Failed to upsert purchase cost rate: {str(e)}")
        raise e


def delete_purchase_rate(
    effective_date: str,
    product_type: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """
    Deletes a purchase cost rate record.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM purchase_cost_log
            WHERE effective_date = ? AND product_type = ?
        """, (effective_date.strip(), product_type))
        
        conn.commit()
        conn.close()
        logger.info(f"Successfully deleted purchase cost rate: {product_type} on {effective_date}")
    except Exception as e:
        logger.error(f"Failed to delete purchase cost rate: {str(e)}")
        raise e
