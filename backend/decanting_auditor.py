#!/usr/bin/env python3
"""
Fuel Tanker Decanting and Depot Transit Shortage Auditor.
Manages database schemas, temperature-volume ASTM corrections, tank ullage checks, and Excel updates.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional
from density_logger import convert_density_to_15c
from tank_calibration import convert_dip_to_liters
from exporter import export_db_to_excel

logger = logging.getLogger("DecantingAuditor")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_decanting_db(db_path: str = DB_PATH):
    """
    Initializes the tanker_receipts audit table inside the SQLite database.
    """
    logger.info(f"Initializing tanker_receipts table in database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tanker_receipts (
        invoice_no TEXT PRIMARY KEY,
        date TEXT,
        tank_lorry_no TEXT,
        product_type TEXT CHECK(product_type IN ('HSD', 'MS')),
        invoice_volume_liters REAL,
        invoice_density_at_15c REAL,
        observed_compartment_dips_mm TEXT,
        observed_density_raw REAL,
        observed_temperature_celsius REAL,
        actual_received_volume_liters REAL,
        transit_shortage_liters REAL
    )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tanker_receipts_date ON tanker_receipts (date)")
    
    conn.commit()
    conn.close()
    logger.info("Table 'tanker_receipts' initialized successfully.")

def get_tank_capacity(tank_id: str, db_path: str = DB_PATH) -> float:
    """
    Returns the maximum volume capacity for a given tank from calibration charts.
    Falls back to a default standard capacity if no chart is loaded.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(volume_liters) FROM tank_calibration_charts WHERE tank_id = ?", (tank_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
        
    # Standard fallback capacities
    if "HSD" in tank_id:
        return 20000.0
    return 15000.0

def query_latest_stock(tank_id: str, db_path: str = DB_PATH) -> float:
    """
    Returns the most recent closing stock recorded in the stock_recon table.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if "HSD" in tank_id:
        cursor.execute("SELECT hsd_closing_dip_liters FROM stock_recon ORDER BY date DESC LIMIT 1")
    else:
        cursor.execute("SELECT ms_closing_dip_liters FROM stock_recon ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] is not None:
        return float(row[0])
    return 0.0

def validate_decanting_space(
    product_type: str,
    actual_received_volume: float,
    current_dip_mm: Optional[float] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Compares the incoming lorry volume against the remaining empty ground capacity (Ullage)
    of the target underground tank, using tank_calibration charts.
    """
    prod = str(product_type).strip().upper()
    tank_id = "Tank_1_HSD" if prod == "HSD" else "Tank_2_MS"
    
    capacity = get_tank_capacity(tank_id, db_path=db_path)
    
    if current_dip_mm is not None:
        current_stock = convert_dip_to_liters(tank_id, current_dip_mm, db_path=db_path)
    else:
        current_stock = query_latest_stock(tank_id, db_path=db_path)
        
    ullage = round(capacity - current_stock, 2)
    space_sufficient = actual_received_volume <= ullage
    
    return {
        "safe": space_sufficient,
        "message": "Clearance approved for decanting." if space_sufficient else "CRITICAL: Insufficient Underground Tank Space for Decanting",
        "tank_id": tank_id,
        "capacity_liters": capacity,
        "current_stock_liters": current_stock,
        "ullage_liters": ullage
    }

def save_tanker_receipt(
    invoice_no: str,
    date_str: str,
    tank_lorry_no: str,
    product_type: str,
    invoice_volume_liters: float,
    invoice_density_at_15c: float,
    observed_compartment_dips_mm: str,
    observed_density_raw: float,
    observed_temperature_celsius: float,
    raw_observed_volume_liters: Optional[float] = None,
    current_dip_mm: Optional[float] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    Calculates temperature-corrected net volume received (adjusted back to 15°C),
    verifies ground space safety, saves record in SQLite database tanker_receipts,
    and automatically rebuilds/syncs the master Excel claims ledger sheet.
    """
    prod = str(product_type).strip().upper()
    if prod not in ["HSD", "MS"]:
        raise ValueError(f"Invalid product type '{product_type}'. Must be 'HSD' or 'MS'.")
        
    # Default raw observed volume to invoice volume if not provided
    raw_obs_vol = float(raw_observed_volume_liters) if raw_observed_volume_liters is not None else float(invoice_volume_liters)
    
    # Standard Temperature-Volume Correction using ASTM Table 53B/53A logic
    d15 = convert_density_to_15c(
        density=float(observed_density_raw),
        temp=float(observed_temperature_celsius),
        product_type=prod,
        method="astm"
    )
    
    # VCF = observed_density_raw / converted_density_15c
    vcf = float(observed_density_raw) / d15 if d15 > 0.0 else 1.0
    
    # Net volume received corrected to 15°C
    actual_received = round(raw_obs_vol * vcf, 2)
    
    # Shortage = Invoice Volume - Net Volume Received
    transit_shortage = round(float(invoice_volume_liters) - actual_received, 2)
    
    # Space Clearance Safety Check
    space_chk = validate_decanting_space(
        product_type=prod,
        actual_received_volume=actual_received,
        current_dip_mm=current_dip_mm,
        db_path=db_path
    )
    
    # Persist in sqlite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO tanker_receipts (
        invoice_no, date, tank_lorry_no, product_type, invoice_volume_liters,
        invoice_density_at_15c, observed_compartment_dips_mm, observed_density_raw,
        observed_temperature_celsius, actual_received_volume_liters, transit_shortage_liters
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_no.strip(),
        date_str.strip(),
        tank_lorry_no.strip(),
        prod,
        float(invoice_volume_liters),
        float(invoice_density_at_15c),
        observed_compartment_dips_mm.strip(),
        float(observed_density_raw),
        float(observed_temperature_celsius),
        actual_received,
        transit_shortage
    ))
    conn.commit()
    conn.close()
    
    # Auto-post to OMC Supplier Advance Ledger
    try:
        from price_registry import get_rates_for_date
        rates = get_rates_for_date(date_str)
        if rates:
            rate = rates.get("hsd_rate") if prod == "HSD" else rates.get("ms_rate")
            gross_invoice_val = round(float(invoice_volume_liters) * (rate or 0.0), 2)
        else:
            rate = 94.27 if prod == "HSD" else 106.31
            gross_invoice_val = round(float(invoice_volume_liters) * rate, 2)

        from omc_reconciler import log_omc_transaction
        log_omc_transaction(
            db_path=db_path,
            date_str=date_str,
            reference_no=invoice_no,
            description="INVOICE_DEDUCTION",
            debit=gross_invoice_val,
            credit=0.0
        )
    except Exception as omc_err:
        logger.warning(f"Failed to log OMC invoice deduction: {str(omc_err)}")
        
    # Auto-sync Excel claims sheet
    try:
        export_db_to_excel()
    except Exception as excel_err:
        logger.warning(f"Failed to auto-sync Excel workbook: {str(excel_err)}")
        
    return {
        "status": "success",
        "invoice_no": invoice_no.strip(),
        "date": date_str.strip(),
        "product_type": prod,
        "volume_correction_factor": round(vcf, 6),
        "actual_received_volume_liters": actual_received,
        "transit_shortage_liters": transit_shortage,
        "space_clearance": space_chk
    }

def get_tanker_receipts(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Retrieves all decanted tanker receipt audit logs.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tanker_receipts ORDER BY date DESC, invoice_no DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
