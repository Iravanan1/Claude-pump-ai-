#!/usr/bin/env python3
"""
Corporate Fleet Card Transaction Reconciliation Module.
Validates credit sales in ledger_entries against portal transactions from oil company statements.
"""

import os
import re
import sqlite3
import logging
import datetime
from datetime import datetime as dt
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
from crypto_vault import decrypt_field

logger = logging.getLogger("FleetCards")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_fleet_cards_db(db_path: str = DB_PATH):
    """
    Initializes the SQLite tables for fleet card reconciliation.
    """
    logger.info(f"Initializing fleet card tables in {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. fleet_card_sales table holds the reconciled outcomes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fleet_card_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            card_program_name TEXT,
            vehicle_no TEXT,
            slip_no_or_invoice TEXT,
            liters_drawn REAL,
            gross_amount REAL,
            portal_match_status TEXT CHECK(portal_match_status IN ('MATCHED', 'UNAUTHORIZED_SWIPE_ALERT', 'MISSING_IN_PORTAL')) DEFAULT 'MISSING_IN_PORTAL'
        )
    """)
    
    # 2. fleet_portal_transactions table holds the imported statement CSV records
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fleet_portal_transactions (
            portal_tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_datetime TEXT,
            card_number TEXT,
            vehicle_no TEXT,
            volume REAL,
            value REAL,
            provider TEXT,
            matched INTEGER DEFAULT 0
        )
    """)
    
    # Create indexes for high-speed queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fleet_sales_date ON fleet_card_sales (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fleet_portal_vehicle ON fleet_portal_transactions (vehicle_no)")
    
    conn.commit()
    conn.close()
    logger.info("Fleet card reconciliation tables successfully initialized!")

def clean_vehicle_no(veh_no: Optional[str]) -> str:
    """
    Cleans vehicle numbers to standardize them (removes hyphens, spaces, uppercase).
    e.g. 'RJ 14-GA 1234' -> 'RJ14GA1234'
    """
    if not veh_no:
        return ""
    return re.sub(r'[^A-Za-z0-9]', '', str(veh_no)).upper().strip()

def import_fleet_portal_csv(file_path: str, provider: str, db_path: str = DB_PATH) -> int:
    """
    Ingests transaction sheets exported from oil company fleet portals.
    Supports flexible case-insensitive column mappings: Date/Time, Card, Vehicle, Volume, Value.
    """
    logger.info(f"Ingesting fleet portal CSV {file_path} for {provider} in {db_path}...")
    df = pd.read_csv(file_path)
    
    # Standardize column header mappings case-insensitively
    col_mapping = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if "date" in col_lower or "time" in col_lower:
            col_mapping["datetime"] = col
        elif "card" in col_lower:
            col_mapping["card"] = col
        elif "vehicle" in col_lower or "reg" in col_lower or "veh" in col_lower:
            col_mapping["vehicle"] = col
        elif "volume" in col_lower or "liter" in col_lower or "qty" in col_lower:
            col_mapping["volume"] = col
        elif "value" in col_lower or "amount" in col_lower or "gross" in col_lower:
            col_mapping["value"] = col
            
    required_cols = {"datetime", "card", "vehicle", "volume", "value"}
    if not required_cols.issubset(col_mapping.keys()):
        missing = required_cols - col_mapping.keys()
        raise ValueError(f"CSV is missing required headers: {list(missing)}. Mapped keys: {col_mapping}")
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    rows_imported = 0
    for _, row in df.iterrows():
        raw_dt = str(row[col_mapping["datetime"]]).strip()
        
        # Standardize date-time parsing
        try:
            # e.g. YYYY-MM-DD HH:MM:SS or DD/MM/YYYY HH:MM
            parsed_dt = pd.to_datetime(raw_dt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            parsed_dt = raw_dt # Fallback to raw string if parsing fails
            
        card_num = str(row[col_mapping["card"]]).strip()
        vehicle = clean_vehicle_no(str(row[col_mapping["vehicle"]]))
        volume = float(row[col_mapping["volume"]])
        value = float(row[col_mapping["value"]])
        
        cursor.execute("""
            INSERT INTO fleet_portal_transactions (transaction_datetime, card_number, vehicle_no, volume, value, provider, matched)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (parsed_dt, card_num, vehicle, volume, value, provider.strip()))
        rows_imported += 1
        
    conn.commit()
    conn.close()
    logger.info(f"✓ Imported {rows_imported} portal transactions successfully.")
    return rows_imported

def parse_time_from_remarks(remarks: str) -> Optional[datetime.time]:
    """
    Attempts to extract time in HH:MM format from remarks.
    """
    if not remarks:
        return None
    match = re.search(r'\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\b', str(remarks))
    if match:
        try:
            return datetime.time(int(match.group(1)), int(match.group(2)))
        except Exception:
            pass
    return None

def reconcile_fleet_transactions(target_date: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Automated cross-matching loop comparing notebook credit sales against portal statements.
    """
    logger.info(f"Running automated fleet card reconciliation for {target_date}...")
    
    # 1. Fetch all credit (udhaar) sales for this date
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # We clear existing reconciled results on this date to support repeat auditing
    cursor.execute("DELETE FROM fleet_card_sales WHERE date = ?", (target_date,))
    conn.commit()
    
    # Query all credit sales
    cursor.execute("""
        SELECT entry_id, date, party_name, vehicle_wheel_no, amount, remarks, created_at
        FROM ledger_entries
        WHERE date = ? AND type = 'udhaar'
    """, (target_date,))
    ledger_rows = cursor.fetchall()
    
    # Filter only fleet accounts
    fleet_sales = []
    for row in ledger_rows:
        try:
            party_dec = decrypt_field(row["party_name"], return_type=str)
            amount_dec = decrypt_field(row["amount"], return_type=float)
        except Exception:
            party_dec = str(row["party_name"])
            amount_dec = float(row["amount"] or 0.0)
            
        party_lower = party_dec.lower().strip()
        remarks_str = str(row["remarks"] or "").lower().strip()
        
        # Check if party name contains fleet indicators
        is_fleet = any(k in party_lower for k in ("fleet", "xtrapower", "drivetrack", "corporate")) or \
                   any(k in remarks_str for k in ("xtrapower", "drivetrack", "fleet card"))
                   
        if is_fleet:
            # Determine reference program name
            program = "IOCL XTRAPOWER"
            if "drivetrack" in party_lower or "drivetrack" in remarks_str:
                program = "HPCL DriveTrack Plus"
            elif "hp" in party_lower:
                program = "HPCL DriveTrack Plus"
                
            fleet_sales.append({
                "entry_id": row["entry_id"],
                "date": row["date"],
                "card_program_name": program,
                "vehicle_no": row["vehicle_wheel_no"],
                "clean_vehicle": clean_vehicle_no(row["vehicle_wheel_no"]),
                "amount": amount_dec,
                "remarks": row["remarks"],
                "created_at": row["created_at"]
            })
            
    summary = {"matched": 0, "unauthorized_swipe": 0, "missing_in_portal": 0}
    
    # 2. Match each fleet credit record against portal transactions
    for sale in fleet_sales:
        clean_v = sale["clean_vehicle"]
        amt = sale["amount"]
        
        # Fetch portal transaction candidates matching amount within +/- 1.0 tolerance and clean date prefix
        cursor.execute("""
            SELECT portal_tx_id, transaction_datetime, card_number, vehicle_no, volume, value, matched
            FROM fleet_portal_transactions
            WHERE transaction_datetime LIKE ?
              AND value BETWEEN ? AND ?
              AND matched = 0
        """, (f"{target_date}%", amt - 1.0, amt + 1.0))
        candidates = cursor.fetchall()
        
        match_found = False
        unauth_found = False
        matched_tx_id = None
        
        # Parse transaction time for ledger entry
        sale_time = parse_time_from_remarks(sale["remarks"])
        if not sale_time and sale["created_at"]:
            try:
                # e.g., '2026-06-01 11:21:01'
                sale_time = dt.strptime(sale["created_at"].split()[1], "%H:%M:%S").time()
            except Exception:
                pass
                
        # Candidate search loop
        for cand in candidates:
            cand_v = cand["vehicle_no"]
            
            # Sub-check A: vehicle numbers match exactly
            if cand_v == clean_v:
                # Sub-check B: Time Delta Verification (+/- 5 minutes)
                if sale_time:
                    try:
                        cand_time = dt.strptime(cand["transaction_datetime"].split()[1], "%H:%M:%S").time()
                        # Calculate difference in seconds
                        t1 = datetime.datetime.combine(datetime.date.today(), sale_time)
                        t2 = datetime.datetime.combine(datetime.date.today(), cand_time)
                        delta_sec = abs((t1 - t2).total_seconds())
                        
                        if delta_sec <= 300:  # 5 minutes in seconds
                            match_found = True
                            matched_tx_id = cand["portal_tx_id"]
                            break
                    except Exception:
                        # Fallback: if time delta parsing crashes, accept vehicle match
                        match_found = True
                        matched_tx_id = cand["portal_tx_id"]
                else:
                    # If ledger has no time tag, match purely on vehicle and amount
                    match_found = True
                    matched_tx_id = cand["portal_tx_id"]
                    break
                    
        # Check for unauthorized swipes if no exact match but we have a matching card/amount with different vehicle
        if not match_found and len(candidates) > 0:
            # Only consider unauthorized swipe if the vehicle numbers actually differ!
            diff_vehicle_cands = [c for c in candidates if c["vehicle_no"] != clean_v]
            if len(diff_vehicle_cands) > 0:
                unauth_found = True
                matched_tx_id = diff_vehicle_cands[0]["portal_tx_id"]
            
        # Commit status mapping
        if match_found:
            status = "MATCHED"
            summary["matched"] += 1
            # Mark portal transaction as matched
            cursor.execute("UPDATE fleet_portal_transactions SET matched = 1 WHERE portal_tx_id = ?", (matched_tx_id,))
        elif unauth_found:
            status = "UNAUTHORIZED_SWIPE_ALERT"
            summary["unauthorized_swipe"] += 1
            # We don't mark as matched because vehicle differed (requires audit)
        else:
            status = "MISSING_IN_PORTAL"
            summary["missing_in_portal"] += 1
            
        # Write to fleet_card_sales table
        cursor.execute("""
            INSERT INTO fleet_card_sales (date, card_program_name, vehicle_no, slip_no_or_invoice, liters_drawn, gross_amount, portal_match_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            sale["date"],
            sale["card_program_name"],
            sale["vehicle_no"],
            f"LID-{sale['entry_id']}",
            round(amt / 94.27, 2),  # Estimate volume based on base rate if missing
            amt,
            status
        ))
        
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "date": target_date,
        "results": summary,
        "total_analyzed": len(fleet_sales)
    }

def get_fleet_reconciliation_status(date_str: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Returns the compiled fleet card reconciliation status records for a specific date.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, card_program_name, vehicle_no, slip_no_or_invoice, liters_drawn, gross_amount, portal_match_status
        FROM fleet_card_sales
        WHERE date = ?
        ORDER BY id ASC
    """, (date_str.strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(r) for r in rows]
