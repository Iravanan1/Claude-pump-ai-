#!/usr/bin/env python3
"""
Monthly Lubricant and Grease Stock Book Reconciliation Engine.
Manages database schemas, sales aggregations, physical counts, and variance evaluations.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("LubeStockBook")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_lube_stock_db(db_path: str = DB_PATH):
    """
    Initializes the lube_inventory_ledger tracking inventory ledger table inside the SQLite database.
    """
    logger.info(f"Initializing lube_inventory_ledger table in database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lube_inventory_ledger (
        item_sku TEXT PRIMARY KEY,
        item_name TEXT,
        opening_stock_units REAL DEFAULT 0.0,
        inward_receipt_units REAL DEFAULT 0.0,
        outward_sold_units REAL DEFAULT 0.0,
        expected_closing_stock REAL DEFAULT 0.0,
        actual_physical_audit_stock REAL DEFAULT NULL,
        inventory_shortage_variance REAL DEFAULT 0.0
    )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Table 'lube_inventory_ledger' initialized successfully.")

def save_lube_inventory_item(
    item_sku: str,
    item_name: str,
    opening_stock: float,
    inward_receipt: float,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Adds or updates a lubricant item in the ledger database.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT OR REPLACE INTO lube_inventory_ledger (
        item_sku, item_name, opening_stock_units, inward_receipt_units,
        outward_sold_units, expected_closing_stock, actual_physical_audit_stock, inventory_shortage_variance
    ) VALUES (
        ?, ?, ?, ?,
        COALESCE((SELECT outward_sold_units FROM lube_inventory_ledger WHERE item_sku = ?), 0.0),
        COALESCE((SELECT expected_closing_stock FROM lube_inventory_ledger WHERE item_sku = ?), 0.0),
        (SELECT actual_physical_audit_stock FROM lube_inventory_ledger WHERE item_sku = ?),
        COALESCE((SELECT inventory_shortage_variance FROM lube_inventory_ledger WHERE item_sku = ?), 0.0)
    )
    """, (
        item_sku.strip(),
        item_name.strip(),
        float(opening_stock),
        float(inward_receipt),
        item_sku.strip(),
        item_sku.strip(),
        item_sku.strip(),
        item_sku.strip()
    ))
    
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "item_sku": item_sku,
        "item_name": item_name,
        "opening_stock_units": opening_stock,
        "inward_receipt_units": inward_receipt
    }

def compute_running_lube_book(
    item_sku: str,
    target_month: str,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Continuous Rollup Logic: Computes running lubricant ledger book values for a target month (YYYY-MM).
    Expected Closing Stock = Opening Stock + Inward Receipts - Outward Sold Units.
    Outward Sold Units is compiled from quantity_sold in inventory_sales table.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Fetch item from ledger
    cursor.execute("SELECT * FROM lube_inventory_ledger WHERE item_sku = ?", (item_sku.strip(),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Lubricant SKU '{item_sku}' not found in the ledger database.")
        
    item = dict(row)
    item_name = item["item_name"]
    opening = float(item["opening_stock_units"] or 0.0)
    inward = float(item["inward_receipt_units"] or 0.0)
    physical = item["actual_physical_audit_stock"]
    if physical is not None:
        physical = float(physical)
        
    # 2. Aggregate sales for target month from inventory_sales
    # Support YYYY-MM prefix match (e.g. '2026-06%')
    cursor.execute("""
        SELECT SUM(quantity_sold) FROM inventory_sales
        WHERE item_name = ? AND date LIKE ?
    """, (item_name, f"{target_month.strip()}%"))
    sales_row = cursor.fetchone()
    outward = float(sales_row[0] or 0.0)
    
    # 3. Calculate expected stock and variance
    expected = round(opening + inward - outward, 2)
    
    variance = 0.0
    if physical is not None:
        variance = round(physical - expected, 2)
        
    # 4. Save results back to ledger table
    cursor.execute("""
        UPDATE lube_inventory_ledger
        SET outward_sold_units = ?,
            expected_closing_stock = ?,
            inventory_shortage_variance = ?
        WHERE item_sku = ?
    """, (outward, expected, variance, item_sku.strip()))
    
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "item_sku": item_sku,
        "item_name": item_name,
        "opening_stock_units": opening,
        "inward_receipt_units": inward,
        "outward_sold_units": outward,
        "expected_closing_stock": expected,
        "actual_physical_audit_stock": physical,
        "inventory_shortage_variance": variance
    }

def commit_physical_stock_count(
    item_sku: str,
    current_physical_count: float,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Audit Variance Checker: Commit physical stock count and compute delta variance.
    inventory_shortage_variance = current_physical_count - expected_closing_stock
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Fetch expected closing stock
    cursor.execute("SELECT expected_closing_stock, item_name, opening_stock_units, inward_receipt_units, outward_sold_units FROM lube_inventory_ledger WHERE item_sku = ?", (item_sku.strip(),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Lubricant SKU '{item_sku}' not found in the ledger database.")
        
    expected = float(row["expected_closing_stock"] or 0.0)
    item_name = row["item_name"]
    opening = float(row["opening_stock_units"] or 0.0)
    inward = float(row["inward_receipt_units"] or 0.0)
    outward = float(row["outward_sold_units"] or 0.0)
    
    # 2. Calculate variance
    variance = round(float(current_physical_count) - expected, 2)
    
    # 3. Update physical stock count and variance in ledger
    cursor.execute("""
        UPDATE lube_inventory_ledger
        SET actual_physical_audit_stock = ?,
            inventory_shortage_variance = ?
        WHERE item_sku = ?
    """, (float(current_physical_count), variance, item_sku.strip()))
    
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "item_sku": item_sku,
        "item_name": item_name,
        "opening_stock_units": opening,
        "inward_receipt_units": inward,
        "outward_sold_units": outward,
        "expected_closing_stock": expected,
        "actual_physical_audit_stock": float(current_physical_count),
        "inventory_shortage_variance": variance
    }

def get_lube_inventory_ledger(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves the entire lubricant inventory stock ledger.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM lube_inventory_ledger ORDER BY item_sku ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
