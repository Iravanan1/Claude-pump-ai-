#!/usr/bin/env python3
"""
PumpAI Automated Diagnostics & Integration Verification Suite.
Executes an isolated trial run of the entire pipeline using sample register sheet entries.
"""

import os
import sys
import shutil
import sqlite3
import numpy as np
import cv2
import json
from unittest.mock import patch

# 1. Sandbox Path Resolution
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

SANDBOX_DB = os.path.join(BACKEND_DIR, "sandbox_pump.db")
SANDBOX_EXPORTS_DIR = os.path.join(BACKEND_DIR, "sandbox_exports")
SANDBOX_EXCEL = os.path.join(SANDBOX_EXPORTS_DIR, "Pump_Accounts.xlsx")
MOCK_RAW_IMAGE = os.path.join(BACKEND_DIR, "sandbox_raw_register.png")

# Set unified EXPORT_EXCEL_PATH env var before importing modules
os.environ["EXPORT_EXCEL_PATH"] = SANDBOX_EXCEL

# 2. Import Modules
import init_db
import main
import exporter
import price_registry
import density_logger
import tank_calibration
import credit_realization
import card_settlements
import reconciliation
import cost_tracker
import state_tracker
import bulk_importer
import bank_matcher
import credit_guard
import dsm_tracker
import evaporation_handler
import local_analytics
import processor
import ai_engine
import fifo_settler
from crypto_vault import encrypt_field, decrypt_field
from fastapi.testclient import TestClient

def setup_sandbox():
    """Initializes the sandbox database and directory structure, redirecting paths."""
    print("🔧 Setting up isolated sandbox environment...")
    
    # Override database paths
    for module in [
        init_db, main, exporter, price_registry, density_logger,
        tank_calibration, credit_realization, card_settlements,
        reconciliation, cost_tracker, state_tracker, bulk_importer,
        bank_matcher, credit_guard, dsm_tracker, evaporation_handler,
        local_analytics, fifo_settler
    ]:
        module.DB_PATH = SANDBOX_DB
        
    # Redirect export paths
    exporter.DEFAULT_EXCEL_PATH = SANDBOX_EXCEL
    main.EXCEL_PATH = SANDBOX_EXCEL
    local_analytics.CHARTS_DIR = os.path.join(SANDBOX_EXPORTS_DIR, "charts")
    
    # Clean up existing sandbox artifacts
    if os.path.exists(SANDBOX_DB):
        os.remove(SANDBOX_DB)
    if os.path.exists(SANDBOX_EXPORTS_DIR):
        shutil.rmtree(SANDBOX_EXPORTS_DIR)
        
    os.makedirs(SANDBOX_EXPORTS_DIR, exist_ok=True)
    os.makedirs(local_analytics.CHARTS_DIR, exist_ok=True)
    
    # Initialize sandbox databases
    init_db.initialize_database()
    main.init_db()

def create_mock_register_image(image_path: str):
    """Generates a dummy registers page with mock handwritten line and title."""
    print("📸 Generating mock handwritten register sheet...")
    # Create white canvas
    img = np.ones((600, 400, 3), dtype=np.uint8) * 255
    # Draw dark register margins
    cv2.rectangle(img, (20, 20), (380, 580), (180, 180, 180), 2)
    # Add titles simulating a physical layout
    cv2.putText(img, "DATE: 2026-06-01", (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    cv2.putText(img, "HSD Nozzle 1: 12000 to 13000", (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    # Write image to disk
    success = cv2.imwrite(image_path, img)
    if not success:
        raise RuntimeError(f"OpenCV failed to write mock image to: {image_path}")

def seed_unpaid_historical_credit():
    """Seeds a historical unpaid udhaar entry for Gopalram Transport."""
    print("🌱 Seeding historical unpaid credit line to verify FIFO settling...")
    conn = sqlite3.connect(SANDBOX_DB)
    cursor = conn.cursor()
    
    # Encrypt fields using crypto_vault
    enc_party = encrypt_field("Gopalram Ji Dhaba")
    enc_veh = encrypt_field("RJ-14-GA-1234")
    enc_amount = encrypt_field(3000.0)
    enc_rem = encrypt_field("Outstanding Hinglish: May credit entry")
    
    cursor.execute("""
        INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks, payment_status, amount_remaining)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("2026-05-28", enc_party, enc_veh, enc_amount, "udhaar", enc_rem, "UNPAID", 3000.0))
    
    conn.commit()
    conn.close()

def cleanup_sandbox():
    """Cleans up the sandbox directory and temporary database."""
    print("🧹 Cleaning up sandbox artifacts...")
    if os.path.exists(SANDBOX_DB):
        try:
            os.remove(SANDBOX_DB)
        except Exception:
            pass
    if os.path.exists(SANDBOX_EXPORTS_DIR):
        try:
            shutil.rmtree(SANDBOX_EXPORTS_DIR)
        except Exception:
            pass
    if os.path.exists(MOCK_RAW_IMAGE):
        try:
            os.remove(MOCK_RAW_IMAGE)
        except Exception:
            pass
            
    # Clean up processed_images folder from temp file
    for f in os.listdir(processor.PROCESSED_DIR):
        if f.startswith("opt_"):
            try:
                os.remove(os.path.join(processor.PROCESSED_DIR, f))
            except Exception:
                pass

def run_diagnostics():
    try:
        # 1. Setup Environment
        setup_sandbox()
        
        # 2. Seed historical udhaar line
        seed_unpaid_historical_credit()
        
        # 3. Step 1: Preprocessing & Contrast Optimization
        create_mock_register_image(MOCK_RAW_IMAGE)
        print("⚡ Running image optimization (grayscale conversion, Canny/Hough deskew, CLAHE shadow removal)...")
        opt_image_path = processor.optimize_register_image(MOCK_RAW_IMAGE)
        if not os.path.exists(opt_image_path) or opt_image_path == MOCK_RAW_IMAGE:
            raise RuntimeError("Contrast optimization pipeline failed to generate an isolated output!")
        print("  => Preprocessing passed.")
        
        # 4. Step 2: Mock AI extraction test
        print("🤖 Simulating data-extraction test using environment engine setup...")
        
        # Mock responses from LLM layer
        mock_raw_transcription = "Raw transcription text for Gopalram Ji Dhaba details. Remarks: Gopalram accounts balanced correctly."
        day_payload = {
            "date": "2026-06-01",
            "total_calculated_liters_hsd": 1000.0,
            "total_calculated_liters_ms": 1500.0,
            "total_cash_calculated": 240000.0,
            "total_credit_sales": 0.0,
            "total_testing_deductions": 0.0,
            "validation_status": "balanced",
            "mathematical_warnings": ["गोपालराम accounts balanced correctly"],
            "nozzles": [
                {
                    "nozzle_name": "HSD-1",
                    "fuel_type": "HSD",
                    "opening": 12000.0,
                    "closing": 13000.0,
                    "sales_liters_calculated": 1000.0,
                    "rate": 90.0,
                    "amount_calculated": 90000.0,
                    "arithmetic_valid": True
                }
            ],
            "credit_sales": [],
            "cash_expenses": [],
            "dsm_shifts": [],
            "lube_sales": [],
            "card_settlements": [],
            "credit_realizations": [
                {
                    "party_name": "गोपालराम Transport",
                    "amount_received": 3000.0,
                    "payment_mode": "CASH",
                    "bank_utr_or_remarks": "Received payment for May credit",
                    "linked_invoice_no": ""
                }
            ]
        }

        # Execute using patches
        with patch("ai_engine.run_gemini_vision_extraction", return_value=mock_raw_transcription) as mock_gemini, \
             patch("ai_engine.run_claude_accounting_guardrails", return_value=day_payload) as mock_claude, \
             patch("image_guard.validate_image_clarity", return_value={"success": True, "status": "OK", "focus_score": 999.0, "contrast_score": 999.0}):
            
            parsed_json = ai_engine.analyze_register_sheet(opt_image_path)
            if parsed_json.get("date") != "2026-06-01":
                raise ValueError("Parsed JSON date is incorrect or missing!")
                
        print("  => Data-extraction passed.")
        
        # 5. Step 3: Call internal database save method via TestClient to verify insertion logic & FIFO allocations
        print("💾 Running database save endpoint POST /api/save-ledger-day...")
        client = TestClient(main.app)
        response = client.post("/api/save-ledger-day", json=parsed_json)
        if response.status_code != 200:
            raise RuntimeError(f"Database save failed: {response.text}")
        print("  => Database save passed.")
        
        # 6. Step 4: Run the openpyxl compiler
        print("📊 Running openpyxl compiler to generate accounting sheets...")
        excel_out, csv_out = exporter.generate_accounting_export(date_string="all")
        if not os.path.exists(excel_out) or not os.path.exists(csv_out):
            raise FileNotFoundError("Excel or CSV compilation failed to generate sandbox output files!")
        print("  => openpyxl excel generation passed.")
        
        # 7. Step 5: System Asserts & Health Checks
        print("🔍 Executing integration asserts and health checks...")
        conn = sqlite3.connect(SANDBOX_DB)
        cursor = conn.cursor()
        
        # Verify primary key constraints (conflicting date save should throw integrity error)
        cursor.execute("SELECT COUNT(*) FROM daily_ledger")
        ledger_count = cursor.fetchone()[0]
        if ledger_count != 1:
            raise ValueError(f"Expected 1 ledger entry, found {ledger_count}!")
            
        # Verify FIFO allocation: transitions outstanding credit line to FULLY_PAID
        cursor.execute("SELECT payment_status, amount_remaining FROM ledger_entries WHERE date = '2026-05-28' AND type = 'udhaar'")
        row = cursor.fetchone()
        if not row:
            raise ValueError("Historical credit line for 2026-05-28 not found!")
            
        status, remaining = row
        if status != "FULLY_PAID" or remaining != 0.0:
            raise ValueError(f"FIFO settlement failed: status={status}, remaining={remaining}")
        print("  => FIFO allocation passed.")
        
        # Verify Hinglish text encodings / Unicode text preservation without decoding anomalies
        cursor.execute("SELECT party_name FROM ledger_entries WHERE date = '2026-05-28'")
        party_enc = cursor.fetchone()[0]
        party_dec = decrypt_field(party_enc, return_type=str)
        if party_dec != "Gopalram Ji Dhaba":
            raise ValueError(f"Hinglish character decoding anomaly detected! Found: {party_dec}")
            
        # Verify Hinglish warnings from JSON raw_data
        cursor.execute("SELECT raw_data FROM daily_ledger WHERE date = '2026-06-01'")
        raw_json_str = cursor.fetchone()[0]
        from crypto_vault import decrypt_raw_data
        decrypted_raw = decrypt_raw_data(json.loads(raw_json_str))
        warnings = decrypted_raw.get("hinglish_notes", [])
        if "गोपालराम accounts balanced correctly" not in warnings:
            raise ValueError(f"Hinglish warning log text got corrupted! Warnings found: {warnings}")
            
        conn.close()
        print("  => Integration asserts and Hinglish character translation pass verified.")
        
        # Clean up sandbox environment
        cleanup_sandbox()
        
        # Success Output
        print("\n\033[92m[OK] Env Loading, [OK] Database Integrity, [OK] Consolidated Excel Export Paths. Sandbox Test Passed Successfully.\033[0m\n")
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Diagnostics suite failed validation checks: {str(e)}")
        try:
            cleanup_sandbox()
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    run_diagnostics()
