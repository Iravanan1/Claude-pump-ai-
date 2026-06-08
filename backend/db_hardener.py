import os
import sqlite3
import logging
from fastapi import APIRouter, HTTPException

# Configure logging
logger = logging.getLogger("DatabaseHardener")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def configure_connection(conn: sqlite3.Connection) -> None:
    """
    Executes optimization and hardening PRAGMAs immediately on a database connection:
    - journal_mode=WAL: Enables Write-Ahead Logging for concurrent reads/writes
    - synchronous=NORMAL: Safe write synchronization optimization for raw data speeds
    - foreign_keys=ON: Enforces strict relation constraint checking
    """
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    except Exception as e:
        logger.warning(f"Failed to apply database hardening PRAGMAs: {str(e)}")

# Global database connection interceptor to apply optimizations to all sqlite3.connect calls
if not hasattr(sqlite3, "_original_connect"):
    sqlite3._original_connect = sqlite3.connect

    def hardened_connect(database, *args, **kwargs):
        conn = sqlite3._original_connect(database, *args, **kwargs)
        try:
            if isinstance(conn, sqlite3.Connection):
                configure_connection(conn)
        except Exception as e:
            logger.warning(f"Failed to configure intercepted SQLite connection: {str(e)}")
        return conn

    sqlite3.connect = hardened_connect
    logger.info("Successfully installed global database connection wrapper for SQLite3 optimization.")

def execute_wal_checkpoint(db_path: str = DB_PATH) -> dict:
    """
    Programmatically triggers a native PRAGMA wal_checkpoint(TRUNCATE) command
    to flush temporary write logs back into the main .db file, reclaiming disk space.
    """
    logger.info(f"Programmatically triggering WAL checkpoint (TRUNCATE) on: {db_path}")
    if not os.path.exists(db_path):
        logger.warning(f"Database file not found at {db_path}. Skipping WAL checkpoint.")
        return {"status": "skipped", "reason": "db_not_found"}

    try:
        # Open connection using original un-wrapped connect to execute checkpoint cleanly
        conn = sqlite3._original_connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        result = cursor.fetchone()
        conn.close()

        if result:
            stats = {
                "status": "success",
                "busy": result[0],
                "log": result[1],
                "checkpointed": result[2]
            }
            logger.info(f"WAL checkpoint successful: busy={result[0]}, log={result[1]} pages, checkpointed={result[2]} pages")
            return stats
        else:
            return {"status": "success", "info": "no_result"}
    except Exception as e:
        logger.error(f"WAL checkpoint execution failed: {str(e)}")
        return {"status": "failed", "error": str(e)}

# FastAPI Router for administration and testing
router = APIRouter(tags=["Database Hardener"])

@router.post("/api/db/checkpoint")
def api_db_checkpoint():
    """Trigger manual WAL checkpoint truncation."""
    res = execute_wal_checkpoint(DB_PATH)
    if res.get("status") == "failed":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res

@router.get("/api/db/status")
def api_db_status():
    """Retrieve active configuration and status of DB PRAGMAs."""
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Database file not found.")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        cursor.execute("PRAGMA synchronous;")
        synchronous = cursor.fetchone()[0]
        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        conn.close()
        return {
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "foreign_keys": foreign_keys
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query DB status: {str(e)}")
