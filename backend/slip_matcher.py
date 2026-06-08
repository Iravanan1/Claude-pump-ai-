#!/usr/bin/env python3
"""
Credit Slip (Chitti) Extraction and Verification Module.
Implements SQLite storage schemas, Gemini multi-object vision transcriptions, 
and automated cross-examination matching algorithms.
"""

import os
import re
import json
import sqlite3
import logging
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types

from crypto_vault import encrypt_field, decrypt_field
from cost_tracker import log_api_transaction, check_budget

logger = logging.getLogger("SlipMatcher")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_slips_db(db_path: str = DB_PATH):
    """
    Initializes the credit_slips SQLite tracking schema with foreign key linkage constraints.
    """
    logger.info(f"Initializing credit_slips table inside: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS credit_slips (
        slip_id TEXT PRIMARY KEY,
        date TEXT,
        party_name TEXT, -- Encrypted at rest
        vehicle_no TEXT,
        amount_or_liters REAL DEFAULT 0.0,
        driver_signature_detected INTEGER DEFAULT 0, -- 0 for False, 1 for True
        matched_ledger_id INTEGER DEFAULT NULL,
        FOREIGN KEY(matched_ledger_id) REFERENCES ledger_entries(entry_id)
    )
    """)
    
    conn.commit()
    conn.close()

def extract_credit_slips_from_image(image_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Invokes Gemini 1.5 Flash to perform multi-object vision detection on physical
    credit slips (Chittis), extracting vehicle plates, currency totals, slip sequence IDs, 
    and identifying driver signatures.
    """
    logger.info("Executing Gemini Vision multi-object parsing on credit slips...")
    
    # Enforce API budget limits before calling model
    check_budget()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is not set.")
        raise ValueError("Missing GEMINI_API_KEY. Please set it in your environment.")
        
    client = genai.Client(api_key=api_key)
    
    prompt = (
        "You are an expert OCR and petrol pump operations vision model.\n"
        "Your task is to analyze the provided image, which contains one or more small handwritten credit slips (Chitti) "
        "placed on a counter, or a single closeup photo of a credit slip.\n"
        "Each credit slip typically records a slip number/sequence ID, a vehicle license plate, a customer party name, "
        "a transaction amount or liters dispensed, and a signature from the driver.\n\n"
        "INSTRUCTIONS:\n"
        "1. Identify each distinct slip independently.\n"
        "2. Extract: 'slip_id' (e.g. sequence number, serial, or 'SLIP-01', 'SLIP-02' sequentially if none printed),\n"
        "   'date' (in YYYY-MM-DD format if visible, otherwise use null),\n"
        "   'party_name' (e.g. Gopalram Ji, RJ Transport, etc.),\n"
        "   'vehicle_no' (license plate),\n"
        "   'amount_or_liters' (the primary numerical charge or liter quantity),\n"
        "   'driver_signature_detected' (boolean, set to true if a signature, hand-written signature line, thumbprint, "
        "   initial, or handwritten mark is detected, false otherwise).\n"
        "3. Output strictly valid JSON. Format the response as a JSON array of objects conforming to this schema:\n"
        "[\n"
        "  {\n"
        "    \"slip_id\": \"TEXT\",\n"
        "    \"date\": \"YYYY-MM-DD or null\",\n"
        "    \"party_name\": \"TEXT\",\n"
        "    \"vehicle_no\": \"TEXT\",\n"
        "    \"amount_or_liters\": NUMBER,\n"
        "    \"driver_signature_detected\": BOOLEAN\n"
        "  }\n"
        "]\n"
        "Generate ONLY the raw JSON text. No markdown wrapping (like ```json), no personal summary, and no comments."
    )
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json"
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type='image/png',
                ),
                prompt
            ],
            config=config
        )
        
        log_api_transaction("gemini", response)
        text = response.text.strip()
        
        # Clean markdown wrapper blocks if returned by chance
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n", "", text)
            text = re.sub(r"\n```$", "", text)
            text = text.strip()
            
        logger.info(f"Raw slips JSON returned: {text}")
        return json.loads(text)
        
    except Exception as e:
        logger.error(f"Credit slip vision extraction failed: {str(e)}")
        raise RuntimeError(f"Chitti Vision Extraction Failed: {str(e)}")

def save_extracted_slips(slips: List[Dict[str, Any]], target_date: str, db_path: str = DB_PATH):
    """
    Saves a batch of extracted credit slips into the SQLite database.
    Normalizes inputs and encrypts party_name at rest to protect customer data.
    """
    init_slips_db(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        for slip in slips:
            slip_id = slip.get("slip_id") or f"CHITTI-{target_date}-{os.urandom(3).hex().upper()}"
            s_date = slip.get("date") or target_date
            party = slip.get("party_name") or "Unknown Party"
            vehicle = slip.get("vehicle_no") or "N/A"
            amt_liters = float(slip.get("amount_or_liters") or 0.0)
            sig = 1 if slip.get("driver_signature_detected") else 0
            
            # Insert or replace slip
            cursor.execute("""
                INSERT OR REPLACE INTO credit_slips 
                (slip_id, date, party_name, vehicle_no, amount_or_liters, driver_signature_detected, matched_ledger_id)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
            """, (
                slip_id,
                s_date,
                encrypt_field(party),
                vehicle,
                amt_liters,
                sig
            ))
            
        conn.commit()
        logger.info(f"✓ Saved {len(slips)} paper credit slips to SQLite successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to save extracted slips: {str(e)}")
        raise e
    finally:
        conn.close()

def cross_reference_slips_to_ledger(target_date: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Performs double-entry cross-examination matching physical credit slips to SQLite register logs.
    
    Algorithm:
        1. Fetch all credit slips recorded for target_date.
        2. Fetch all ledger_entries (type = 'udhaar') recorded for target_date.
        3. Match based on a combinations of:
           - Normalized vehicle strings (alphanumeric, ignoring spaces and case).
           - Numerical amount or liter quantities matching within delta of 0.01.
        4. Update SQLite slips table linking matched entry_ids.
        5. Return discrepancy matrix classifying statuses:
           - MATCHED
           - UNRECORDED_SLIP_ALERT (paper slip exists, but is not in register)
           - MISSING_SLIP_PROOF (register entry exists, but has no physical slip proof)
    """
    init_slips_db(db_path)
    
    # 1. Fetch Credit Slips
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT slip_id, date, party_name, vehicle_no, amount_or_liters, driver_signature_detected, matched_ledger_id
        FROM credit_slips
        WHERE date = ?
    """, (target_date,))
    slip_rows = cursor.fetchall()
    
    slips = []
    for row in slip_rows:
        try:
            party_name = decrypt_field(row[2], return_type=str)
        except Exception:
            party_name = str(row[2] or "")
            
        slips.append({
            "slip_id": row[0],
            "date": row[1],
            "party_name": party_name,
            "vehicle_no": row[3] or "N/A",
            "amount_or_liters": float(row[4] or 0.0),
            "driver_signature_detected": bool(row[5]),
            "matched_ledger_id": row[6],
            "status": "UNRECORDED_SLIP_ALERT" # Default status until matched
        })
        
    # 2. Fetch Ledger Entries
    cursor.execute("""
        SELECT entry_id, date, party_name, vehicle_wheel_no, amount, type, remarks
        FROM ledger_entries
        WHERE date = ? AND type = 'udhaar'
    """, (target_date,))
    ledger_rows = cursor.fetchall()
    
    ledger_entries = []
    for row in ledger_rows:
        try:
            party_name = decrypt_field(row[2], return_type=str)
            amount = decrypt_field(row[4], return_type=float)
        except Exception:
            party_name = str(row[2] or "")
            amount = float(row[4] or 0.0)
            
        ledger_entries.append({
            "entry_id": row[0],
            "date": row[1],
            "party_name": party_name,
            "vehicle_wheel_no": row[3] or "N/A",
            "amount": amount,
            "remarks": row[6] or "",
            "status": "MISSING_SLIP_PROOF" # Default status until matched
        })
        
    # 3. Match Logic Iteration
    norm = lambda v: "".join(c for c in str(v).upper() if c.isalnum()).strip()
    
    # Store matched pairings in a transaction block
    try:
        cursor.execute("BEGIN TRANSACTION")
        
        for slip in slips:
            s_vehicle_norm = norm(slip["vehicle_no"])
            s_val = slip["amount_or_liters"]
            
            best_match = None
            for entry in ledger_entries:
                if entry["status"] == "MATCHED":
                    continue
                    
                e_vehicle_norm = norm(entry["vehicle_wheel_no"])
                e_val = entry["amount"]
                
                # Check normalized vehicle match AND amount proximity
                vehicle_matches = (s_vehicle_norm == e_vehicle_norm) and (s_vehicle_norm != "" and s_vehicle_norm != "NA")
                amount_matches = abs(s_val - e_val) < 0.01
                
                # Fallback to loose match if names match and amount is exact
                loose_matches = (slip["party_name"].lower() in entry["party_name"].lower() or entry["party_name"].lower() in slip["party_name"].lower()) and amount_matches
                
                if (vehicle_matches and amount_matches) or loose_matches:
                    best_match = entry
                    break
                    
            if best_match:
                slip["status"] = "MATCHED"
                slip["matched_ledger_id"] = best_match["entry_id"]
                best_match["status"] = "MATCHED"
                
                # Write back match linkage to SQLite
                cursor.execute("""
                    UPDATE credit_slips
                    SET matched_ledger_id = ?
                    WHERE slip_id = ?
                """, (best_match["entry_id"], slip["slip_id"]))
                
        conn.commit()
        logger.info(f"✓ Completed cross-reference calculations for {target_date}.")
        
    except Exception as commit_err:
        conn.rollback()
        logger.error(f"Failed to commit cross-reference bindings: {commit_err}")
        conn.close()
        raise commit_err
        
    conn.close()
    
    return {
        "status": "success",
        "date": target_date,
        "slips": slips,
        "ledger_entries": ledger_entries
    }
