import db_hardener
import sqlite3
import os
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DatabaseInit")

DB_PATH = "ledger.db"

def initialize_database():
    """
    Initializes the local SQLite database and creates daily_summary and ledger_entries tables
    optimized for high-speed append operations.
    """
    logger.info(f"Initializing SQLite database at {os.path.abspath(DB_PATH)}...")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Create daily_summary table
        logger.info("Creating 'daily_summary' table...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_hsd_liters REAL DEFAULT 0.0,
            total_ms_liters REAL DEFAULT 0.0,
            total_cash_calculated REAL DEFAULT 0.0,
            total_credit_sales REAL DEFAULT 0.0,
            total_testing_deductions REAL DEFAULT 0.0,
            is_verified INTEGER DEFAULT 0, -- 0 for False, 1 for True
            meter_replaced INTEGER DEFAULT 0, -- 0 for False, 1 for True
            replacement_offset_liters REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # 2. Create ledger_entries table
        logger.info("Creating 'ledger_entries' table...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            party_name TEXT,
            vehicle_wheel_no TEXT,
            amount REAL DEFAULT 0.0,
            type TEXT, -- e.g., 'udhaar', 'expense', 'bank_drop'
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Create an index on date in ledger_entries for fast querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date ON ledger_entries (date)")
        
        conn.commit()
        conn.close()
        logger.info("Database tables initialized triumphantly!")
        
        # Initialize fuel_rates table
        try:
            from price_registry import init_rates_db
            init_rates_db()
        except Exception as rates_err:
            logger.warning(f"Failed to auto-initialize fuel_rates table: {str(rates_err)}")

        # Initialize density_register compliance table
        try:
            from density_logger import init_density_db
            init_density_db(DB_PATH)
        except Exception as density_err:
            logger.warning(f"Failed to auto-initialize density_register table: {str(density_err)}")

        # Initialize tank_calibration_charts table
        try:
            from tank_calibration import init_calibration_db
            init_calibration_db(DB_PATH)
        except Exception as cal_err:
            logger.warning(f"Failed to auto-initialize tank_calibration_charts table: {str(cal_err)}")

        # Initialize credit_realizations table
        try:
            from credit_realization import init_realization_db
            init_realization_db(DB_PATH)
        except Exception as real_err:
            logger.warning(f"Failed to auto-initialize credit_realizations table: {str(real_err)}")

        # Initialize bank statement cross-referencing tables
        try:
            from bank_matcher import init_bank_matcher_db
            init_bank_matcher_db(DB_PATH)
        except Exception as bm_err:
            logger.warning(f"Failed to auto-initialize bank_matcher tables: {str(bm_err)}")

        # Initialize staff_advances table
        try:
            from staff_ledger import init_staff_ledger_db
            init_staff_ledger_db(DB_PATH)
        except Exception as staff_err:
            logger.warning(f"Failed to auto-initialize staff_advances table: {str(staff_err)}")

        # Initialize fleet_card tables
        try:
            from fleet_cards import init_fleet_cards_db
            init_fleet_cards_db(DB_PATH)
        except Exception as fleet_err:
            logger.warning(f"Failed to auto-initialize fleet_cards table: {str(fleet_err)}")

        # Initialize decanting tanker_receipts table
        try:
            from decanting_auditor import init_decanting_db
            init_decanting_db(DB_PATH)
        except Exception as decant_err:
            logger.warning(f"Failed to auto-initialize tanker_receipts table: {str(decant_err)}")

        # Initialize OMC advance ledger table
        try:
            from omc_reconciler import init_omc_reconciler_db
            init_omc_reconciler_db(DB_PATH)
        except Exception as omc_init_err:
            logger.warning(f"Failed to auto-initialize omc_advance_ledger table: {str(omc_init_err)}")

        # Initialize lube inventory ledger table
        try:
            from lube_stock_book import init_lube_stock_db
            init_lube_stock_db(DB_PATH)
        except Exception as lube_stock_err:
            logger.warning(f"Failed to auto-initialize lube_inventory_ledger table: {str(lube_stock_err)}")

        # Initialize FTS search virtual table
        try:
            from text_search import init_fts_db
            init_fts_db(DB_PATH)
        except Exception as fts_err:
            logger.warning(f"Failed to auto-initialize fts virtual table: {str(fts_err)}")

        # Initialize premium product database schemas
        try:
            from premium_products import migrate_premium_product_columns
            migrate_premium_product_columns(DB_PATH)
        except Exception as prem_err:
            logger.warning(f"Failed to migrate database for premium products columns: {str(prem_err)}")

        # Run database schema migrations
        try:
            from migrations import apply_schema_updates
            apply_schema_updates(DB_PATH)
        except Exception as migration_err:
            logger.error(f"Failed to apply database migrations at initialization: {str(migration_err)}")
            raise migration_err

        # Initialize underground tank tilt profiler tables
        try:
            from dip_profiler import init_dip_profiler_db
            init_dip_profiler_db(DB_PATH)
        except Exception as dip_prof_err:
            logger.warning(f"Failed to auto-initialize dip profiler tables: {str(dip_prof_err)}")

    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        raise e

if __name__ == "__main__":
    initialize_database()
