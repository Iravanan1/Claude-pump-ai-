"""
Non-Fuel Asset and Lubricant Inventory Sales Module.
Manages ancillary sales databases and aggregates revenues to update daily expectations.
"""

import os
import sqlite3
from typing import List, Dict
from logger import logger

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_lube_db(db_path: str = DB_PATH):
    """
    Initializes the inventory_sales table inside the SQLite database.
    """
    logger.info(f"Initializing inventory_sales table in SQLite database at {os.path.abspath(db_path)}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_sales (
            date TEXT,
            item_name TEXT,
            quantity_sold REAL DEFAULT 0.0,
            unit_price REAL DEFAULT 0.0,
            total_item_revenue REAL DEFAULT 0.0,
            UNIQUE(date, item_name)
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'inventory_sales' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'inventory_sales' table: {str(e)}")
        raise e

def save_lube_sale(
    date_str: str,
    item_name: str,
    quantity_sold: float,
    unit_price: float,
    total_item_revenue: float = 0.0,
    db_path: str = DB_PATH
):
    """
    Saves or updates an ancillary/lubricant sale entry in the database.
    """
    logger.info(f"Saving inventory sale: {item_name} on {date_str}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO inventory_sales (
                date, item_name, quantity_sold, unit_price, total_item_revenue
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            date_str.strip(),
            item_name.strip(),
            float(quantity_sold),
            float(unit_price),
            float(total_item_revenue)
        ))
        conn.commit()
        conn.close()
        logger.info(f"Inventory sale for '{item_name}' saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save inventory sale: {str(e)}")
        raise e

def get_lube_sales_by_date(date_str: str, db_path: str = DB_PATH) -> List[Dict]:
    """
    Retrieves all lubricant and ancillary inventory sales for a specific date, sorted alphabetically.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT item_name, quantity_sold, unit_price, total_item_revenue
            FROM inventory_sales WHERE date = ? ORDER BY item_name ASC
        """, (date_str.strip(),))
        rows = cursor.fetchall()
        conn.close()
        
        sales = []
        for r in rows:
            sales.append({
                "date": date_str,
                "item_name": r[0],
                "quantity_sold": float(r[1] or 0.0),
                "unit_price": float(r[2] or 0.0),
                "total_item_revenue": float(r[3] or 0.0)
            })
        return sales
    except Exception as e:
        logger.error(f"Failed to fetch inventory sales for date {date_str}: {str(e)}")
        return []

def delete_lube_sales_by_date(date_str: str, db_path: str = DB_PATH):
    """
    Deletes all lubricant sales recorded for a specific date (for clean overrides).
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM inventory_sales WHERE date = ?", (date_str.strip(),))
        conn.commit()
        conn.close()
        logger.info(f"Deleted inventory sales for date {date_str}.")
    except Exception as e:
        logger.error(f"Failed to delete inventory sales for date {date_str}: {str(e)}")
        raise e

def verify_inventory_totals(date_string: str, db_path: str = DB_PATH) -> float:
    """
    Inventory Validation Guard:
    Aggregates gross revenue from inventory sales on date_string and appends
    it to the day's total cash expected calculations inside daily_summary.
    """
    logger.info(f"Executing Inventory Validation Guard for date: {date_string}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Aggregate the sum of lube revenue
        cursor.execute("SELECT SUM(total_item_revenue) FROM inventory_sales WHERE date = ?", (date_string.strip(),))
        lube_sum = cursor.fetchone()[0] or 0.0
        
        # 2. Check if a summary block exists in daily_summary
        cursor.execute("SELECT total_cash_calculated FROM daily_summary WHERE date = ?", (date_string.strip(),))
        summary_row = cursor.fetchone()
        
        if summary_row:
            # 3. Resolve the base expected fuel cash from daily_ledger's total_amount_inr
            cursor.execute("SELECT total_amount_inr FROM daily_ledger WHERE date = ?", (date_string.strip(),))
            ledger_row = cursor.fetchone()
            
            base_fuel_cash = 0.0
            if ledger_row:
                base_fuel_cash = float(ledger_row[0] or 0.0)
            
            # Combine fuel totalizer cash + lubricant sales gross cash
            new_total_expected_cash = base_fuel_cash + lube_sum
            
            # 4. Update the daily_summary table with the combined total cash expected calculations
            cursor.execute("""
                UPDATE daily_summary 
                SET total_cash_calculated = ?
                WHERE date = ?
            """, (new_total_expected_cash, date_string.strip()))
            
            conn.commit()
            logger.info(f"Validation Guard successfully combined totals for {date_string}: {new_total_expected_cash} INR.")
            
        conn.close()
        return lube_sum
    except Exception as e:
        logger.error(f"Inventory validation guard failed for date {date_string}: {str(e)}")
        return 0.0
