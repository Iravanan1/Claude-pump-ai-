import sqlite3
import logging
import os
import re
from price_registry import get_rates_for_date
from premium_products import resolve_variant_rate

logger = logging.getLogger("DiscountManager")

def ensure_table(db_path: str):
    """
    Ensures customer_contracts table exists in the database.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_contracts (
            party_name TEXT PRIMARY KEY,
            discount_type TEXT NOT NULL CHECK(discount_type IN ('FIXED_PER_LITER', 'PERCENTAGE_REDUCTION')),
            discount_value REAL NOT NULL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to ensure customer_contracts table: {str(e)}")

def get_all_contracts(db_path: str) -> list:
    """
    Returns all customer contracts from the database.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, discount_type, discount_value, created_at, updated_at FROM customer_contracts ORDER BY party_name ASC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch contracts: {str(e)}")
        return []

def get_contract(party_name: str, db_path: str) -> dict or None:
    """
    Returns a contract for a specific party name.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT party_name, discount_type, discount_value, created_at, updated_at FROM customer_contracts WHERE LOWER(TRIM(party_name)) = LOWER(TRIM(?))", (party_name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to fetch contract for {party_name}: {str(e)}")
        return None

def upsert_contract(party_name: str, discount_type: str, discount_value: float, db_path: str):
    """
    Inserts or replaces a customer contract.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO customer_contracts (party_name, discount_type, discount_value, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (party_name.strip(), discount_type, float(discount_value)))
        conn.commit()
        conn.close()
        # Invalidate autocomplete cache
        try:
            from main import _invalidate_suggest_cache
            _invalidate_suggest_cache()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to upsert contract for {party_name}: {str(e)}")
        raise e

def delete_contract(party_name: str, db_path: str):
    """
    Deletes a contract.
    """
    ensure_table(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM customer_contracts WHERE LOWER(TRIM(party_name)) = LOWER(TRIM(?))", (party_name,))
        conn.commit()
        conn.close()
        # Invalidate autocomplete cache
        try:
            from main import _invalidate_suggest_cache
            _invalidate_suggest_cache()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to delete contract for {party_name}: {str(e)}")
        raise e

def apply_contract_discounts(credit_sales: list, date_str: str, db_path: str) -> list:
    """
    Applies custom contract discounts to credit sales list.
    Calculates:
      - base_amount (original amount before discount)
      - discount_applied (computed net discount)
      - base_rate (resolved price per liter)
    Reduces credit['amount'] by discount_applied.
    """
    ensure_table(db_path)
    
    # Load all contracts for O(1) matching
    try:
        contracts_list = get_all_contracts(db_path)
        contracts = {c["party_name"].strip().lower(): c for c in contracts_list}
    except Exception as e:
        logger.error(f"Error fetching contracts in discount application: {str(e)}")
        contracts = {}

    if not contracts:
        # No contracts configured, just fill defaults and return
        for credit in credit_sales:
            if "base_amount" not in credit or credit["base_amount"] is None:
                credit["base_amount"] = float(credit.get("amount") or 0.0)
            if "discount_applied" not in credit or credit["discount_applied"] is None:
                credit["discount_applied"] = 0.0
            if "base_rate" not in credit or credit["base_rate"] is None:
                credit["base_rate"] = _resolve_base_rate_for_credit(credit, date_str, db_path)
        return credit_sales

    # Load rates for this date
    day_rates = None
    if date_str:
        try:
            day_rates = get_rates_for_date(date_str)
        except Exception as rates_err:
            logger.warning(f"Failed to load rates for date {date_str}: {str(rates_err)}")

    for credit in credit_sales:
        party_name = credit.get("party_name") or ""
        clean_party = party_name.strip().lower()
        
        # Get base amount before any calculations
        base_amount = float(credit.get("base_amount") or credit.get("amount") or 0.0)
        
        # Resolve fuel rate
        base_rate = _resolve_base_rate_for_credit(credit, date_str, db_path, day_rates)
        
        if clean_party in contracts:
            contract = contracts[clean_party]
            discount_type = contract["discount_type"]
            discount_value = float(contract["discount_value"])
            
            # Resolve liters drawn
            liters_drawn = float(credit.get("liters_drawn") or credit.get("liters") or 0.0)
            
            # Fallback for liters
            if liters_drawn == 0.0:
                # Try parsing from remarks (e.g. "HSD 50L" or "50 liters")
                remarks = str(credit.get("remarks") or "")
                liters_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:L|liters|litres|लीटर)\b", remarks, re.IGNORECASE)
                if liters_match:
                    liters_drawn = float(liters_match.group(1))
                elif base_rate > 0.0:
                    # Calculate liters based on rate
                    liters_drawn = base_amount / base_rate
            
            # Compute discount
            discount_applied = 0.0
            if discount_type == "FIXED_PER_LITER":
                discount_applied = liters_drawn * discount_value
            elif discount_type == "PERCENTAGE_REDUCTION":
                discount_applied = base_amount * (discount_value / 100.0)
                
            discount_applied = round(discount_applied, 2)
            final_amount = round(max(0.0, base_amount - discount_applied), 2)
            
            # Modify the credit sale object
            credit["amount"] = final_amount
            credit["base_amount"] = base_amount
            credit["discount_applied"] = discount_applied
            credit["base_rate"] = base_rate
            if liters_drawn > 0:
                credit["liters_drawn"] = round(liters_drawn, 2)
            
            # Annotate remarks to indicate discount
            remarks = credit.get("remarks") or ""
            disc_label = f" (Discount: {discount_applied:.2f})"
            if disc_label not in remarks and discount_applied > 0:
                # Keep it clean, don't repeatedly append
                clean_remarks = re.sub(r"\s*\(Discount: \d+(?:\.\d+)?\)", "", remarks)
                credit["remarks"] = clean_remarks + disc_label
        else:
            # Contract does not exist for this party
            credit["base_amount"] = base_amount
            credit["discount_applied"] = 0.0
            credit["base_rate"] = base_rate
            
    return credit_sales

def _resolve_base_rate_for_credit(credit: dict, date_str: str, db_path: str, day_rates: dict = None) -> float:
    """
    Helper to resolve the fuel rate for a credit sale.
    """
    # 1. Check if rate is explicitly in the credit sale dict
    if "rate" in credit and credit["rate"]:
        try:
            return float(credit["rate"])
        except ValueError:
            pass

    # 2. Try to get rates from DB if not passed
    if day_rates is None and date_str:
        try:
            day_rates = get_rates_for_date(date_str)
        except Exception:
            pass

    # Determine fuel type from remarks
    remarks = str(credit.get("remarks") or "").strip().upper()
    fuel_type = "REGULAR_HSD" # Default HSD
    
    if "PREMIUM_HSD" in remarks or "XTRAGREEN" in remarks:
        fuel_type = "PREMIUM_HSD"
    elif "PREMIUM_MS" in remarks or "XP95" in remarks or "XP 95" in remarks or "SPEED" in remarks:
        fuel_type = "PREMIUM_MS"
    elif "MS" in remarks or "PETROL" in remarks:
        fuel_type = "REGULAR_MS"
    elif "HSD" in remarks or "DIESEL" in remarks:
        fuel_type = "REGULAR_HSD"
        
    if day_rates:
        try:
            rate = resolve_variant_rate(day_rates, fuel_type)
            if rate:
                return float(rate)
        except Exception:
            pass
            
    # Default fallbacks
    return 94.27 if "HSD" in fuel_type else 106.31
