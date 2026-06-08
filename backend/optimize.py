import os
import sqlite3
from logger import logger

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def optimize_database(db_path=None):
    """
    Executes structural database optimization:
    1. Chronological date, case-insensitive party_name, and vehicle index creations.
    2. VACUUM and ANALYZE routines to reclaim dead space, defragment, and update query plans.
    """
    if not db_path:
        # Check if pump_accounts.db exists; if so, optimize it. Otherwise, default to ledger.db
        alt_db = os.path.join(BACKEND_DIR, "pump_accounts.db")
        if os.path.exists(alt_db):
            db_path = alt_db
        else:
            db_path = DEFAULT_DB_PATH
            
    logger.info(f"Starting SQLite database optimization suite against: {os.path.abspath(db_path)}...")
    
    if not os.path.exists(db_path):
        logger.warning(f"Database file not found at {db_path}. Skipping optimizations.")
        return False
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Performance Indexing
        logger.info("Executing Step 1: Creating performance indexes...")
        
        # Chronological index on ledger_entries(date)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_date 
            ON ledger_entries (date)
        """)
        
        # Case-insensitive index on ledger_entries(party_name)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_party_nocase 
            ON ledger_entries (party_name COLLATE NOCASE)
        """)
        
        # Rapid vehicle index on ledger_entries(vehicle_wheel_no)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_vehicle 
            ON ledger_entries (vehicle_wheel_no)
        """)
        
        conn.commit()
        logger.info("Chronological, customer, and vehicle indices built successfully.")
        
        # 2. Production Compactor: VACUUM and ANALYZE
        logger.info("Executing Step 2: Database compaction (VACUUM & ANALYZE)...")
        
        # SQLite VACUUM cannot run inside a transaction transaction.
        # We must set connection isolation level to None to run it in autocommit mode.
        conn.isolation_level = None
        cursor.execute("VACUUM")
        cursor.execute("ANALYZE")
        
        conn.close()
        logger.info("Database optimization and compaction completed triumphantly!")
        return True
        
    except Exception as e:
        logger.error(f"Failed to optimize database at {db_path}: {str(e)}")
        return False

if __name__ == "__main__":
    optimize_database()
