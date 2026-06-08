import os
import db_hardener
import sys
import sqlite3
import shutil
import logging
import argparse
from datetime import datetime

# Add backend directory to path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BACKEND_DIR)

from processor import optimize_register_image
from ai_engine import analyze_register_sheet
from init_db import initialize_database
from crypto_vault import encrypt_field, encrypt_raw_data

# Setup unified logging
from logger import logger
from cleanup import flush_memory

# State machine — persistent batch job tracker
from state_tracker import (
    init_state_db,
    calculate_file_hash,
    is_completed,
    upsert_job,
    mark_processing,
    mark_completed,
    mark_failed,
    JobStatus,
    get_batch_stats,
)

# Constants
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")
DEFAULT_PHOTOS_DIR = os.path.join(BACKEND_DIR, "historical_register_photos")
FLAGGED_DIR = os.path.join(BACKEND_DIR, "flagged_records")

# Ensure target directories exist
os.makedirs(DEFAULT_PHOTOS_DIR, exist_ok=True)
os.makedirs(FLAGGED_DIR, exist_ok=True)

# Initialize state tracker DB
init_state_db(DB_PATH)

def init_metadata_table():
    """
    Creates a processed_files metadata table in ledger.db to log image hashes,
    and ensures daily_ledger exists for visualization in the frontend.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            filename TEXT UNIQUE,
            file_hash TEXT UNIQUE,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Ensure daily_ledger exists in case main.py hasn't run yet
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            total_sales_liters REAL,
            total_amount_inr REAL,
            cash_tender REAL,
            upi_tender REAL,
            paytm_transfers REAL,
            card_tender REAL,
            udhaar_sales REAL,
            expenses_amount REAL,
            validation_status TEXT,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to create metadata table: {str(e)}")

def is_already_processed(filename: str, file_hash: str) -> bool:
    """
    Checks if a filename or image hash already exists in processed_files.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM processed_files WHERE filename = ? OR file_hash = ?", 
            (filename, file_hash)
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        logger.error(f"Check duplicate failed for {filename}: {str(e)}")
        return False

def record_processed_file(filename: str, file_hash: str):
    """
    Logs the filename and hash in processed_files to prevent double processing.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO processed_files (filename, file_hash) VALUES (?, ?)", 
            (filename, file_hash)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Log processed file failed for {filename}: {str(e)}")

def commit_to_ledger(data: dict, is_verified: bool):
    """
    Commits structured AI-audited results directly to daily_summary, ledger_entries,
    and daily_ledger tables.
    """
    import json
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Insert into daily_summary
        date_str = data.get("date") or datetime.now().strftime("%Y-%m-%d")
        total_hsd = float(data.get("total_calculated_liters_hsd") or 0.0)
        total_ms = float(data.get("total_calculated_liters_ms") or 0.0)
        total_cash = float(data.get("total_cash_calculated") or 0.0)
        total_credit = float(data.get("total_credit_sales") or 0.0)
        total_testing = float(data.get("total_testing_deductions") or 0.0)
        verified_flag = 1 if is_verified else 0
        
        # Check daily summary columns
        cursor.execute("PRAGMA table_info(daily_summary)")
        summary_cols = [c[1] for c in cursor.fetchall()]
        
        summary_data = {
            "date": date_str,
            "total_hsd_liters": total_hsd,
            "total_ms_liters": total_ms,
            "total_cash_calculated": total_cash,
            "total_credit_sales": total_credit,
            "total_testing_deductions": total_testing,
            "is_verified": verified_flag,
        }
        
        # Check if extended splits or meter replacements are in schema
        if "total_regular_hsd_liters" in summary_cols:
            summary_data["total_regular_hsd_liters"] = float(data.get("total_regular_hsd_liters") or 0.0)
            summary_data["total_premium_hsd_liters"] = float(data.get("total_premium_hsd_liters") or 0.0)
            summary_data["total_regular_ms_liters"] = float(data.get("total_regular_ms_liters") or 0.0)
            summary_data["total_premium_ms_liters"] = float(data.get("total_premium_ms_liters") or 0.0)
            
        if "meter_replaced" in summary_cols:
            summary_data["meter_replaced"] = 1 if bool(data.get("meter_replaced") or False) else 0
            summary_data["replacement_offset_liters"] = float(data.get("replacement_offset_liters") or 0.0)
            
        fields = list(summary_data.keys())
        placeholders = ", ".join(["?"] * len(fields))
        columns_str = ", ".join(fields)
        query = f"INSERT OR REPLACE INTO daily_summary ({columns_str}) VALUES ({placeholders})"
        cursor.execute(query, tuple(summary_data[f] for f in fields))
        
        # 2. Insert credit ledger transactions ('udhaar')
        for credit in data.get("credit_sales", []):
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, 'udhaar', ?)
            """, (
                date_str, 
                encrypt_field(credit.get("party_name") or "Unknown Party"), 
                credit.get("vehicle_no") or "N/A", 
                encrypt_field(float(credit.get("amount") or 0.0)),
                credit.get("remarks") or "HSD credit sale"
            ))
            
        # 3. Insert cash expenses ('expense')
        for expense in data.get("cash_expenses", []):
            cursor.execute("""
                INSERT INTO ledger_entries (date, party_name, vehicle_wheel_no, amount, type, remarks)
                VALUES (?, ?, ?, ?, 'expense', ?)
            """, (
                date_str, 
                encrypt_field(expense.get("party_name") or "Office Expense"), 
                "N/A", 
                encrypt_field(float(expense.get("amount") or 0.0)),
                expense.get("remarks") or "Cash expense"
            ))
            
        # 4. Insert into daily_ledger for unified frontend compatibility
        total_sales_liters = total_hsd + total_ms
        expenses_amount = sum(float(e.get("amount") or 0.0) for e in data.get("cash_expenses", []))
        val_status_str = "valid" if is_verified else "needs_review"
        
        # Build UI payload structure
        nozzle_list = []
        for n in data.get("nozzles", []):
            nozzle_list.append({
                "nozzle_name": n.get("nozzle_name"),
                "fuel_type": n.get("fuel_type"),
                "opening": float(n.get("opening") or 0.0),
                "closing": float(n.get("closing") or 0.0),
                "sales_liters_calculated": float(n.get("net_sales_liters") or n.get("calculated_flow") or 0.0),
                "rate": float(n.get("rate") or 0.0),
                "amount_calculated": float(n.get("amount_calculated") or 0.0),
                "arithmetic_valid": n.get("is_valid", True),
                "discrepancy_details": n.get("math_warning"),
                "meter_replaced": bool(n.get("meter_replaced") or False),
                "replacement_offset_liters": float(n.get("replacement_offset_liters") or 0.0)
            })
            
        raw_payload = {
            "date": date_str,
            "nozzles": nozzle_list,
            "hinglish_notes": data.get("mathematical_warnings", []),
            "total_sales_liters": total_sales_liters,
            "total_amount_inr": total_cash,
            "validation_status": val_status_str,
            "audit_explanation": ", ".join(data.get("mathematical_warnings", [])) or "Batch imported entry",
            "cash_tender": max(0.0, total_cash - total_credit - expenses_amount),
            "upi_tender": 0.0,
            "paytm_transfers": 0.0,
            "card_tender": 0.0,
            "udhaar_sales": total_credit,
            "expenses_amount": expenses_amount,
            "meter_replaced": bool(data.get("meter_replaced") or False),
            "replacement_offset_liters": float(data.get("replacement_offset_liters") or 0.0)
        }
        
        encrypted_raw_payload = encrypt_raw_data(raw_payload)
        raw_json_str = json.dumps(encrypted_raw_payload, ensure_ascii=False)
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ledger 
            (date, total_sales_liters, total_amount_inr, cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, expenses_amount, validation_status, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str,
            total_sales_liters,
            total_cash,
            raw_payload["cash_tender"],
            0.0,
            0.0,
            0.0,
            total_credit,
            expenses_amount,
            val_status_str,
            raw_json_str
        ))
        
        conn.commit()
        conn.close()
        
        # Trigger daily ledger backup dispatcher in background
        try:
            from backup_dispatcher import dispatch_daily_ledger_backup_background
            dispatch_daily_ledger_backup_background(date_str, DB_PATH)
        except Exception as dispatch_err:
            logger.warning(f"Failed to trigger backup dispatch post-commit: {str(dispatch_err)}")
    except Exception as e:
        logger.error(f"DB commit failed for date {data.get('date')}: {str(e)}")
        raise e

def print_progress(processed: int, total: int, flagged: int, current_file: str = ""):
    """
    Renders a simple terminal progress bar.
    """
    width = 30
    filled = int(width * processed // total) if total > 0 else 0
    bar = "█" * filled + "░" * (width - filled)
    
    # Clean output line formatting
    sys.stdout.write(f"\r[{bar}] Processed Day {processed}/{total} | Flagged for review: {flagged} | Active: {current_file[:20]:<20}")
    sys.stdout.flush()

def run_bulk_import(photos_dir: str):
    """
    Loops through historical register photos, runs the preprocessor & AI pipeline,
    and saves results cleanly.
    """
    # 1. Initialize databases and configurations
    initialize_database()
    init_metadata_table()
    init_state_db(DB_PATH)          # ensure batch_status table exists

    # 2. Locate image files
    valid_extensions = (".png", ".jpg", ".jpeg", ".webp")
    image_files = [
        f for f in os.listdir(photos_dir)
        if os.path.isfile(os.path.join(photos_dir, f)) and f.lower().endswith(valid_extensions)
    ]
    
    total_files = len(image_files)
    if total_files == 0:
        print(f"\nNo register images discovered inside folder '{photos_dir}'. Add photos and run again!")
        return
        
    print(f"\nDiscovered {total_files} register images inside '{photos_dir}'. Starting batch import...")
    
    processed_count = 0
    flagged_count = 0
    
    for filename in sorted(image_files):
        filepath = os.path.join(photos_dir, filename)

        # ── A. Content hash (state machine key) ──────────────────────────
        file_hash = calculate_file_hash(filepath)
        if not file_hash:
            logger.warning(f"Could not hash '{filename}' — skipping.")
            continue

        # ── B. Register job in state machine (idempotent upsert) ─────────
        upsert_job(file_hash, filename, db_path=DB_PATH)

        # ── C. Skip if already COMPLETED ─────────────────────────────────
        if is_completed(file_hash, db_path=DB_PATH):
            processed_count += 1
            print_progress(processed_count, total_files, flagged_count, f"Skipping (done) {filename}")
            logger.info(f"state_tracker: SKIP (COMPLETED) — '{filename}'")
            continue

        # ── D. Also check old processed_files table (backward compat) ────
        if is_already_processed(filename, file_hash):
            # Promote to COMPLETED in the new table
            mark_completed(file_hash, db_path=DB_PATH)
            processed_count += 1
            print_progress(processed_count, total_files, flagged_count, f"Skipping (legacy) {filename}")
            continue

        try:
            print_progress(processed_count + 1, total_files, flagged_count, filename)

            # ── E. Transition → PROCESSING ────────────────────────────────
            mark_processing(file_hash, db_path=DB_PATH)

            # Clarity Check Guardrail (PROMPT 46)
            from image_guard import validate_image_clarity
            from state_tracker import mark_preprocessing_failed
            from archiver import archive_processed_file
            
            clarity_res = validate_image_clarity(filepath)
            if not clarity_res["success"]:
                reason_msg = f"Clarity check failed: {clarity_res['status']} (Focus score: {clarity_res['focus_score']:.2f}, Contrast: {clarity_res['contrast_score']:.2f})"
                mark_preprocessing_failed(file_hash, reason_msg, db_path=DB_PATH)
                
                # Slide file straight to requires_human_review folder
                archive_processed_file(filepath, status="FAILED_PREPROCESSING")
                
                flagged_count += 1
                processed_count += 1
                print_progress(processed_count, total_files, flagged_count, f"Bad quality {filename}")
                continue

            # Step 1: Run image through OpenCV optimization
            optimized_path = optimize_register_image(filepath)

            # Step 2: Run Gemini + Claude audit pipeline
            try:
                accounting_data = analyze_register_sheet(optimized_path)
            except Exception as ocr_err:
                # OCR / AI failure
                mark_failed(file_hash, str(ocr_err), is_ocr=True, db_path=DB_PATH)
                flagged_count += 1
                shutil.copy(filepath, os.path.join(FLAGGED_DIR, filename))
                processed_count += 1
                print_progress(processed_count, total_files, flagged_count, f"OCR error {filename}")
                continue

            # Step 3: Evaluate mathematical warnings and status
            validation_status = accounting_data.get("validation_status") or "balanced"
            has_warnings      = len(accounting_data.get("mathematical_warnings") or []) > 0
            is_audit_passed   = (validation_status == "balanced") and (not has_warnings)

            # Commit structured records
            commit_to_ledger(accounting_data, is_audit_passed)

            # Log in old processed_files table (backward compat)
            record_processed_file(filename, file_hash)

            if not is_audit_passed:
                # Math issues found — mark with appropriate failure state,
                # but still flag for human review rather than re-running.
                flagged_count += 1
                shutil.copy(filepath, os.path.join(FLAGGED_DIR, filename))
                math_reason = "; ".join(accounting_data.get("mathematical_warnings") or ["math discrepancy"])
                mark_failed(file_hash, math_reason, is_ocr=False, db_path=DB_PATH)
            else:
                # ── F. Transition → COMPLETED ─────────────────────────────
                mark_completed(file_hash, db_path=DB_PATH)

            processed_count += 1
            print_progress(processed_count, total_files, flagged_count, filename)

        except Exception as e:
            # Unexpected exception — mark OCR failure as a safe catch-all
            err_msg = str(e)
            mark_failed(file_hash, err_msg, is_ocr=True, db_path=DB_PATH)
            flagged_count  += 1
            shutil.copy(filepath, os.path.join(FLAGGED_DIR, filename))
            processed_count += 1
            print_progress(processed_count, total_files, flagged_count, f"Error {filename}")

        finally:
            # Force garbage collection after every file
            flush_memory()
            
    # Run structural database optimization, compaction, and WAL checkpoint truncation silently after bulk import finishes
    try:
        from optimize import optimize_database
        optimize_database(DB_PATH)
    except Exception as opt_err:
        logger.warning(f"Silently skipped post-bulk-import database optimization: {str(opt_err)}")

    try:
        from db_hardener import execute_wal_checkpoint
        execute_wal_checkpoint(DB_PATH)
    except Exception as cp_err:
        logger.warning(f"Silently skipped post-bulk-import database WAL checkpoint: {str(cp_err)}")

    try:
        from db_vacuum import execute_db_vacuum_background
        execute_db_vacuum_background(DB_PATH)
    except Exception as vac_err:
        logger.warning(f"Silently skipped post-bulk-import database storage vacuum: {str(vac_err)}")
        
    print(f"\n\n==================================================")
    print(f"      BATCH BULK IMPORT COMPLETED SUCCESSFULLY!    ")
    print(f"==================================================")
    print(f"✓ Total Photos Scanned: {total_files}")
    print(f"✓ Automatically Verified & Saved: {total_files - flagged_count}")
    print(f"✓ Flagged for Manual Review: {flagged_count} (copied to /flagged_records)")
    print(f"==================================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Headless Batch Petrol Pump Daily Register Bulk Importer.")
    parser.add_argument(
        "--dir", 
        type=str, 
        default=DEFAULT_PHOTOS_DIR, 
        help="Local directory path containing historical register images."
    )
    args = parser.parse_args()
    
    run_bulk_import(args.dir)
