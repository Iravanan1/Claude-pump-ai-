#!/usr/bin/env python3
"""
PumpAI Historical Backlog Batch Processing Controller
=====================================================
Processes a directory of backlog images and PDFs in chronological order,
uses state_tracker for crash-safe recovery, and implements a local Ollama
fallback mechanism in case cloud APIs are offline or rate-limited.
"""

import os
import sys
import logging
import sqlite3
from typing import List, Dict, Any, Generator

try:
    from logger import logger
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("backfill_orchestrator")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from state_tracker import (
    calculate_file_hash,
    is_completed,
    upsert_job,
    mark_processing,
    mark_completed,
    mark_failed,
    mark_preprocessing_failed,
    JobStatus,
    DB_PATH
)
from processor import optimize_register_image
from ai_engine import analyze_register_sheet
from bulk_importer import commit_to_ledger

def scan_and_sort_backlog(photos_dir: str) -> List[str]:
    """
    Scans the backlog directory for valid file types (.jpg, .png, .pdf)
    and sorts them in ascending chronological order based on file metadata timestamps,
    falling back to filename order.
    """
    if not os.path.isdir(photos_dir):
        logger.error(f"Backlog folder directory not found: '{photos_dir}'")
        return []
        
    valid_extensions = (".png", ".jpg", ".jpeg", ".webp", ".pdf")
    files = [
        f for f in os.listdir(photos_dir)
        if os.path.isfile(os.path.join(photos_dir, f)) and f.lower().endswith(valid_extensions)
    ]
    
    # Sort by file modification time (metadata timestamp) ascending, with filename fallback
    files.sort(key=lambda f: (os.path.getmtime(os.path.join(photos_dir, f)), f))
    return files

def run_backfill_orchestration(
    photos_dir: str,
    db_path: str = DB_PATH,
) -> Generator[str, None, None]:
    """
    Durable, resilient processing loop that iterates over the backlog files.
    Yields real-time progress updates.
    """
    # 1. Scan and sort
    files = scan_and_sort_backlog(photos_dir)
    total_files = len(files)
    
    if total_files == 0:
        yield f"Batch Progress: 0/0 sheets processed. Current Mode: Idle"
        return

    # ── Pre-batch safety snapshot ────────────────────────────────────────────
    # Take a timestamped binary copy of the production DB before touching any
    # data.  A failed snapshot is logged but never blocks the batch run.
    try:
        from db_rollback import create_pre_batch_snapshot
        snap_path = create_pre_batch_snapshot(db_path=db_path, label="pre_batch")
        logger.info(f"Pre-batch snapshot created: '{snap_path}'")
    except Exception as snap_err:
        logger.warning(
            f"Could not create pre-batch snapshot (non-fatal): {snap_err}"
        )
    # ────────────────────────────────────────────────────────────────────────

    processed_count = 0

    for filename in files:

        filepath = os.path.join(photos_dir, filename)
        file_hash = calculate_file_hash(filepath)
        
        if not file_hash:
            logger.warning(f"Failed to calculate hash for: '{filename}' - skipping.")
            continue
            
        # Register in state tracker
        upsert_job(file_hash, filename, db_path=db_path)
        
        # Check if already processed
        if is_completed(file_hash, db_path=db_path):
            processed_count += 1
            yield f"Batch Progress: {processed_count}/{total_files} sheets processed successfully. Current Mode: Cloud API (Skipped completed)"
            continue
            
        # Start processing
        mark_processing(file_hash, db_path=db_path)
        current_mode = "Cloud API (Gemini/Claude)"
        
        try:
            # Step 1: Preprocessing (Skip CV2 optimization for PDFs)
            is_pdf = filename.lower().endswith(".pdf")
            if is_pdf:
                processed_path = filepath
            else:
                try:
                    processed_path = optimize_register_image(filepath)
                except Exception as prep_err:
                    mark_preprocessing_failed(file_hash, f"Preprocessing failed: {str(prep_err)}", db_path=db_path)
                    processed_count += 1
                    yield f"Batch Progress: {processed_count}/{total_files} sheets processed successfully. Current Mode: Preprocessing Failed ({filename})"
                    continue
            
            # Step 2: Attempt extraction with Cloud APIs, failover to Local Ollama
            accounting_data = None
            try:
                # Primary Cloud Attempt
                accounting_data = analyze_register_sheet(
                    processed_path,
                    vision_engine="gemini",
                    logic_engine="claude"
                )
            except Exception as cloud_err:
                logger.warning(
                    f"Cloud API failed for '{filename}': {str(cloud_err)}. "
                    "Failover to Local On-Device AI (Ollama)..."
                )
                current_mode = "Local On-Device AI"
                
                try:
                    # Failover Local Attempt
                    accounting_data = analyze_register_sheet(
                        processed_path,
                        vision_engine="local",
                        logic_engine="local"
                    )
                except Exception as local_err:
                    # Both failed
                    error_msg = f"Both Cloud and Local engines failed. Cloud: {str(cloud_err)} | Local: {str(local_err)}"
                    mark_failed(file_hash, error_msg, is_ocr=True, db_path=db_path)
                    processed_count += 1
                    yield f"Batch Progress: {processed_count}/{total_files} sheets processed successfully. Current Mode: Failed ({filename})"
                    continue

            # Step 3: Commit to database ledger
            if accounting_data:
                validation_status = accounting_data.get("validation_status") or "balanced"
                has_warnings = len(accounting_data.get("mathematical_warnings") or []) > 0
                is_audit_passed = (validation_status == "balanced") and (not has_warnings)
                
                # Use a custom connection or let bulk_importer open it
                commit_to_ledger(accounting_data, is_audit_passed)
                
                # Mark status as completed
                mark_completed(file_hash, db_path=db_path)
                processed_count += 1
                
                # Emit progress update
                yield f"Batch Progress: {processed_count}/{total_files} sheets processed successfully. Current Mode: {current_mode}"
                
        except Exception as loop_err:
            mark_failed(file_hash, f"Unexpected loop error: {str(loop_err)}", is_ocr=True, db_path=db_path)
            processed_count += 1
            yield f"Batch Progress: {processed_count}/{total_files} sheets processed successfully. Current Mode: Failed ({filename})"

    # Trigger database compaction and indexing optimization post backfill orchestration
    try:
        from db_vacuum import execute_db_vacuum_background
        execute_db_vacuum_background(db_path)
    except Exception as vac_err:
        logger.warning(f"Failed to trigger db vacuum: {str(vac_err)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 backfill_orchestrator.py <photos_dir>")
        sys.exit(1)
        
    photos_dir = sys.argv[1]
    print(f"Starting backlog backfill orchestration for directory: '{photos_dir}'...")
    for progress_msg in run_backfill_orchestration(photos_dir):
        print(progress_msg)
