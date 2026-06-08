#!/usr/bin/env python3
"""
PumpAI Database Maintenance and Storage Optimization Engine
===========================================================
Reclaims unused space (compaction) and defragments Full-Text Search (FTS) index trees.
"""

import os
import sqlite3
import logging
import threading

try:
    from logger import logger
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("db_vacuum")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def execute_db_vacuum(db_path: str = DB_PATH) -> bool:
    """
    Safely executes auto-vacuum, database file compaction,
    and FTS index optimization.
    """
    logger.info(f"Starting database maintenance and storage optimization on: {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        
        # 1. File Compaction
        # auto_vacuum cannot be changed after table creation unless we run VACUUM,
        # but running PRAGMA auto_vacuum = INCREMENTAL; then VACUUM; is standard in SQLite.
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        conn.execute("VACUUM;")
        logger.info("  => File compaction (auto_vacuum + VACUUM) executed successfully.")
        
        # 2. FTS Optimization
        # Run FTS optimization query to merge index trees
        try:
            conn.execute("INSERT INTO ledger_fts(ledger_fts) VALUES('optimize');")
            logger.info("  => Full-Text Search (ledger_fts) index optimization complete.")
        except sqlite3.OperationalError as fts_err:
            # Catch operational errors if ledger_fts table does not exist or isn't initialized yet
            logger.warning(f"  => FTS optimization skipped or failed: {str(fts_err)}")
            
        conn.close()
        logger.info("Database vacuum and maintenance completed successfully!")
        return True
    except Exception as e:
        logger.error(f"Database vacuum and maintenance failed: {str(e)}")
        return False

def execute_db_vacuum_background(db_path: str = DB_PATH) -> threading.Thread:
    """
    Spawns execute_db_vacuum inside a safe daemon background thread
    so that it does not block the calling process.
    """
    t = threading.Thread(target=execute_db_vacuum, args=(db_path,), daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PumpAI DB Vacuum & Optimization Tool.")
    parser.add_argument("--db", type=str, default=DB_PATH, help="Path to SQLite database.")
    args = parser.parse_args()
    
    execute_db_vacuum(args.db)
