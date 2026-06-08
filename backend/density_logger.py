"""
Daily Fuel Density and Quality Compliance Log Module.
Manages database schemas, ASTM conversion math, and quality compliance threshold guards.
"""

import os
import sqlite3
import math
import logging
from typing import List, Dict, Any, Tuple

# Setup logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DensityLogger")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_density_db(db_path: str = DB_PATH):
    """
    Initializes the density_register compliance table inside SQLite database.
    """
    logger.info(f"Initializing density_register table in SQLite database at {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS density_register (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_type TEXT, -- 'HSD' or 'MS'
            observed_temperature_celsius REAL,
            observed_density_raw REAL,
            converted_density_at_15c REAL,
            invoice_density_reference REAL,
            permissible_variation_passed INTEGER DEFAULT 1, -- 1=True, 0=False
            UNIQUE(date, product_type)
        )
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_density_register_date ON density_register (date)
        """)
        
        conn.commit()
        conn.close()
        logger.info("Table 'density_register' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'density_register' table: {str(e)}")
        raise e

def convert_density_to_15c(density: float, temp: float, product_type: str, method: str = 'astm') -> float:
    """
    Converts raw observed density at ambient temperature to standard density at 15°C.
    Supports:
    - 'astm': High-fidelity ASTM Table 53B (HSD) and 53A (MS) non-linear iterative solver.
    - 'linear': Standard Indian retail outlet linear correction approximations:
      HSD (Diesel) correction factor: 0.7 per °C
      MS (Petrol) correction factor: 0.9 per °C
    """
    if not density:
        return 0.0
        
    delta_t = float(temp) - 15.0
    prod = str(product_type).strip().upper()
    
    if method == 'linear':
        # Linear retail approximation: D15 = D_obs + C * (T_obs - 15)
        # Expansion coefficient: MS = 0.9, HSD = 0.7
        coeff = 0.7 if prod == 'HSD' else 0.9
        d15_est = density + coeff * delta_t
        return round(d15_est, 2)
        
    else:
        # High-fidelity ASTM Table 53B / 53A fixed-point iterative solver
        # Standard constants (K0, K1) for refined products (53B / 53A products):
        # MS (Petrol) typically uses Group A constants: K0 = 346.0122, K1 = 0.4388
        # HSD (Diesel) typically uses Group B constants: K0 = 186.9696, K1 = 0.4862
        if prod == 'MS':
            K0 = 346.0122
            K1 = 0.4388
        else: # HSD
            K0 = 186.9696
            K1 = 0.4862
            
        d15 = float(density)
        # Fixed point iteration solver converges extremely quickly (within 5-10 runs)
        for _ in range(15):
            if d15 <= 0.0:
                break
            alpha_15 = (K0 + K1 * d15) / (d15 ** 2)
            vcf = math.exp(-alpha_15 * delta_t * (1.0 + 0.8 * alpha_15 * delta_t))
            d15 = density / vcf
            
        return round(d15, 2)

def save_density_record(
    date_str: str,
    product_type: str,
    temp: float,
    raw_density: float,
    invoice_ref: float,
    db_path: str = DB_PATH,
    method: str = 'astm'
) -> Dict[str, Any]:
    """
    Calculates standard density at 15°C, asserts variation compliance against invoice references
    within +/- 3 kg/m³ standard range, and commits the records to SQLite idempotently.
    """
    logger.info(f"Saving density compliance record for {product_type} on {date_str}...")
    try:
        prod = str(product_type).strip().upper()
        if prod not in ['HSD', 'MS']:
            raise ValueError(f"Invalid product type '{product_type}'. Must be 'HSD' or 'MS'.")
            
        # Convert observed ambient density to standard 15°C
        converted_density = convert_density_to_15c(
            density=float(raw_density),
            temp=float(temp),
            product_type=prod,
            method=method
        )
        
        # Calculate variation: Converted Density - Invoice Reference
        variation = round(converted_density - float(invoice_ref), 2)
        
        # Statutory threshold compliance limit checks (+/- 3 kg/m³)
        passed = 1 if abs(variation) <= 3.0 else 0
        
        if not passed:
            logger.warning(
                f"🚨 HIGH-PRIORITY REGULATORY ALERT: Density variation for {prod} on {date_str} "
                f"exceeded permissible limit! Variation: {variation} kg/m³ (Limit +/- 3.0)."
            )
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT OR REPLACE INTO density_register (
            date, product_type, observed_temperature_celsius, observed_density_raw,
            converted_density_at_15c, invoice_density_reference, permissible_variation_passed
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str.strip(),
            prod,
            float(temp),
            float(raw_density),
            converted_density,
            float(invoice_ref),
            passed
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"Density compliance log saved successfully. Passed: {passed == 1}")
        
        return {
            "date": date_str.strip(),
            "product_type": prod,
            "observed_temperature_celsius": float(temp),
            "observed_density_raw": float(raw_density),
            "converted_density_at_15c": converted_density,
            "invoice_density_reference": float(invoice_ref),
            "variation": variation,
            "permissible_variation_passed": bool(passed)
        }
    except Exception as e:
        logger.error(f"Failed to save density compliance record: {str(e)}")
        raise e

def get_density_records(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Pulls chronological density records sorted by date.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT date, product_type, observed_temperature_celsius, observed_density_raw,
               converted_density_at_15c, invoice_density_reference, permissible_variation_passed
        FROM density_register
        ORDER BY date DESC, product_type ASC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            variation = round(r["converted_density_at_15c"] - r["invoice_density_reference"], 2)
            results.append({
                "date": r["date"],
                "product_type": r["product_type"],
                "observed_temperature_celsius": r["observed_temperature_celsius"],
                "observed_density_raw": r["observed_density_raw"],
                "converted_density_at_15c": r["converted_density_at_15c"],
                "invoice_density_reference": r["invoice_density_reference"],
                "variation": variation,
                "permissible_variation_passed": bool(r["permissible_variation_passed"])
            })
        return results
    except Exception as e:
        logger.error(f"Failed to fetch density compliance records: {str(e)}")
        return []
