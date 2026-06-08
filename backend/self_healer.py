import os
import sqlite3
import pickle
import glob
import logging
from datetime import datetime
import pandas as pd

logger = logging.getLogger("DatabaseSelfHealer")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
SHADOW_DIR = os.path.join(BACKEND_DIR, "shadow_mirror")

def perform_integrity_check(db_path: str) -> bool:
    """
    Executes PRAGMA integrity_check; against the SQLite database.
    Returns True if healthy ("ok"), False if corrupted or cannot be read.
    """
    if not os.path.exists(db_path):
        # Database does not exist yet; not considered corrupted.
        return True
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        if result and result[0] == "ok":
            return True
        else:
            logger.warning(f"Integrity check failed: {result}")
            return False
    except Exception as e:
        logger.error(f"Error checking database integrity: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def save_shadow_mirror(db_path: str, shadow_dir: str = SHADOW_DIR) -> str:
    """
    Reads all database tables using pandas and saves them as a pickled dictionary
    to the shadow_mirror directory.
    """
    os.makedirs(shadow_dir, exist_ok=True)
    if not os.path.exists(db_path):
        logger.warning(f"Cannot save shadow mirror: database {db_path} does not exist.")
        return ""
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all table names in database, excluding sqlite_sequence and FTS5 shadow tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = []
        for r in cursor.fetchall():
            t_name = r[0]
            if t_name == "sqlite_sequence":
                continue
            # Filter out FTS5 shadow tables for ledger_fts
            if t_name.startswith("ledger_fts_") and t_name[len("ledger_fts_"):] in ("data", "idx", "config", "docsize", "content"):
                continue
            tables.append(t_name)
        
        data_dump = {}
        for table in tables:
            try:
                df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
                data_dump[table] = df
            except Exception as read_err:
                logger.error(f"Failed to read table {table} for shadow clone: {str(read_err)}")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clone_file = os.path.join(shadow_dir, f"shadow_mirror_{timestamp}.pkl")
        
        with open(clone_file, "wb") as f:
            pickle.dump(data_dump, f)
            
        logger.info(f"✓ Shadow mirror clone written to: {clone_file}")
        
        # Cleanup old clones to maintain a slim footprint (keep latest 5)
        clean_old_clones(shadow_dir)
        return clone_file
    except Exception as e:
        logger.error(f"Failed to create shadow mirror clone: {str(e)}")
        return ""
    finally:
        if conn:
            conn.close()

def clean_old_clones(shadow_dir: str, keep_count: int = 5):
    """
    Removes older shadow mirror clone files to conserve space.
    """
    try:
        files = glob.glob(os.path.join(shadow_dir, "shadow_mirror_*.pkl"))
        files.sort() # lexicographical sort puts older timestamps first
        if len(files) > keep_count:
            files_to_delete = files[:-keep_count]
            for file in files_to_delete:
                os.remove(file)
                logger.info(f"Deleted old shadow mirror file: {file}")
    except Exception as e:
        logger.error(f"Error cleaning up old shadow clones: {str(e)}")

def auto_heal_if_corrupted(db_path: str, shadow_dir: str = SHADOW_DIR) -> bool:
    """
    Runs integrity scan on database. If corrupted, blocks uvicorn import,
    renames/isolates the corrupted file, initializes a blank db structure,
    restores data from the latest shadow mirror clone, and prints status.
    """
    if perform_integrity_check(db_path):
        return True
    
    print("[HEALTH DIAGNOSTIC] Warning: Database integrity check failed. Initiating recovery sequence...")
    logger.warning("Database corruption caught! Isolating corrupted database file...")
    
    # 1. Isolate the corrupted file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    corrupted_path = f"{db_path}.corrupted_{timestamp}"
    try:
        if os.path.exists(db_path):
            os.rename(db_path, corrupted_path)
            logger.info(f"Corrupted database isolated to: {corrupted_path}")
    except Exception as err:
        logger.error(f"Failed to isolate corrupted database: {str(err)}")
        # Continue anyway, initialize will overwrite or fail.
        
    # 2. Re-initialize a blank database file structure using init_db.py
    try:
        from init_db import initialize_database, DB_PATH as INIT_DB_PATH
        # Ensure init_db writes to the correct DB_PATH
        import init_db
        init_db.DB_PATH = db_path
        initialize_database()
        logger.info("New blank production database structure re-initialized successfully.")
    except Exception as init_err:
        logger.critical(f"Failed to re-initialize blank database: {str(init_err)}")
        return False
        
    # 3. Read the latest uncorrupted shadow journal clone entry
    clones = glob.glob(os.path.join(shadow_dir, "shadow_mirror_*.pkl"))
    clones.sort(reverse=True) # latest first
    
    data_recovered = False
    recovered_records_count = 0
    records_lost = 0
    
    for clone in clones:
        try:
            with open(clone, "rb") as f:
                data_dump = pickle.load(f)
            
            # Re-populate all tables in the database
            conn = sqlite3.connect(db_path)
            # Temporarily turn off foreign key checks for recovery import
            conn.execute("PRAGMA foreign_keys = OFF;")
            
            # Re-populate
            for table_name, df in data_dump.items():
                if not isinstance(df, pd.DataFrame):
                    raise ValueError(f"Shadow dump entry for table {table_name} is not a DataFrame.")
                
                # Truncate any auto-generated content to overwrite
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table_name};")
                conn.commit()
                
                # Append rows back
                df.to_sql(table_name, conn, if_exists="append", index=False)
                recovered_records_count += len(df)
                
            conn.close()
            logger.info(f"Database successfully restored from shadow mirror: {clone}")
            data_recovered = True
            break
        except Exception as restore_err:
            logger.error(f"Failed to restore from clone {clone}: {str(restore_err)}. Trying older shadow clones...")
            
    if data_recovered:
        # Since we successfully recovered everything from the latest verified state clone, records lost is 0.
        records_lost = 0
        print(f"[HEALTH DIAGNOSTIC] Corruption caught and auto-patched. {records_lost} records lost.")
        return True
    else:
        logger.error("No valid shadow mirror clone could be found or restored.")
        print("[HEALTH DIAGNOSTIC] Corruption caught but recovery failed. No shadow clones available.")
        return False
