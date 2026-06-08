#!/usr/bin/env python3
"""
Schema Evolution & Database Migration Controller.
Coordinates raw SQLite database migrations sequentially with transactional rollbacks.
"""

import os
import sqlite3
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Migrations")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def apply_schema_updates(db_path: str = DEFAULT_DB_PATH):
    """
    Checks the active structural schema version of the SQLite database and executes
    sequential transactional migrations using raw SQLite commands.
    """
    logger.info(f"Checking database migrations for: {os.path.abspath(db_path)}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. Create sys_version metadata tracking table if missing
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sys_version (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """)
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize sys_version metadata table: {str(e)}")
        conn.close()
        raise e

    # 2. Retrieve the current schema version
    cursor.execute("SELECT value FROM sys_version WHERE key = 'version'")
    row = cursor.fetchone()
    
    if row is None:
        # Determine starting version based on physical column layouts
        # If ledger_entries already exists, check if transaction_source is present
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ledger_entries'")
        has_ledger_entries = cursor.fetchone() is not None
        
        if has_ledger_entries:
            cursor.execute("PRAGMA table_info(ledger_entries)")
            columns = [c[1] for c in cursor.fetchall()]
            if 'transaction_source' in columns:
                current_version = 2
            else:
                current_version = 1
        else:
            # Brand new database starts at version 1 (before updates)
            current_version = 1
            
        cursor.execute("INSERT INTO sys_version (key, value) VALUES ('version', ?)", (current_version,))
        conn.commit()
        logger.info(f"No existing version detected. Initialized sys_version metadata with version = {current_version}")
    else:
        current_version = row[0]
        
    logger.info(f"Active database schema version: {current_version}")
    
    # 3. Apply updates sequentially
    
    # --- MIGRATION: VERSION 1 -> VERSION 2 ---
    if current_version == 1:
        logger.info("Upgrading database schema: Version 1 -> Version 2...")
        
        # Enforce existence of ledger_entries base table before altering
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            party_name TEXT,
            vehicle_wheel_no TEXT,
            amount REAL DEFAULT 0.0,
            type TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        
        try:
            # Wrap all structural changes inside a strict transaction
            conn.execute("BEGIN TRANSACTION")
            
            # Add transaction_source column to ledger_entries
            logger.info("Altering table 'ledger_entries' to add 'transaction_source'...")
            cursor.execute("ALTER TABLE ledger_entries ADD COLUMN transaction_source TEXT DEFAULT 'manual';")
            
            # Increment the version inside the metadata table
            cursor.execute("UPDATE sys_version SET value = 2 WHERE key = 'version'")
            
            conn.commit()
            current_version = 2
            logger.info("✓ Upgrade successful: Database schema upgraded cleanly to Version 2.")
        except Exception as migration_err:
            # Immediate fail-safe rollback to preserve historical ledger data
            conn.rollback()
            logger.error(f"❌ Migration 1 -> 2 failed midway. Transaction rolled back safely. Error: {str(migration_err)}")
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 2 -> VERSION 3 (FIFO Credit Balancing Columns) ---
    if current_version == 2:
        logger.info("Upgrading database schema: Version 2 -> Version 3 (FIFO columns)...")
        
        try:
            conn.execute("BEGIN TRANSACTION")
            
            # Add payment tracking columns for FIFO credit settlement
            cursor.execute("PRAGMA table_info(ledger_entries)")
            existing_cols = {c[1] for c in cursor.fetchall()}
            
            if "payment_status" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'payment_status'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN payment_status TEXT DEFAULT 'UNPAID';")
            
            if "amount_remaining" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'amount_remaining'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN amount_remaining REAL DEFAULT NULL;")
            
            if "linked_payment_id" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'linked_payment_id'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN linked_payment_id TEXT DEFAULT NULL;")
            
            # Increment the version
            cursor.execute("UPDATE sys_version SET value = 3 WHERE key = 'version'")
            
            conn.commit()
            current_version = 3
            logger.info("✓ Upgrade successful: Database schema upgraded cleanly to Version 3 (FIFO).")
        except Exception as migration_err:
            conn.rollback()
            logger.error(f"❌ Migration 2 -> 3 failed midway. Transaction rolled back safely. Error: {str(migration_err)}")
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 3 -> VERSION 4 (Special Contracts and Discount columns) ---
    if current_version == 3:
        logger.info("Upgrading database schema: Version 3 -> Version 4 (Special Contracts)...")
        try:
            conn.execute("BEGIN TRANSACTION")
            
            cursor.execute("PRAGMA table_info(ledger_entries)")
            existing_cols = {c[1] for c in cursor.fetchall()}
            
            if "base_amount" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'base_amount'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN base_amount TEXT DEFAULT NULL;")
            
            if "discount_applied" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'discount_applied'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN discount_applied TEXT DEFAULT NULL;")
                
            if "base_rate" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'base_rate'...")
                cursor.execute("ALTER TABLE ledger_entries ADD COLUMN base_rate TEXT DEFAULT NULL;")
            
            # Create customer_contracts table
            logger.info("Creating 'customer_contracts' table...")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_contracts (
                party_name TEXT PRIMARY KEY,
                discount_type TEXT NOT NULL CHECK(discount_type IN ('FIXED_PER_LITER', 'PERCENTAGE_REDUCTION')),
                discount_value REAL NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            # Increment the version
            cursor.execute("UPDATE sys_version SET value = 4 WHERE key = 'version'")
            
            conn.commit()
            current_version = 4
            logger.info("✓ Upgrade successful: Database schema upgraded cleanly to Version 4.")
        except Exception as migration_err:
            conn.rollback()
            logger.error(f"❌ Migration 3 -> 4 failed midway. Transaction rolled back safely. Error: {str(migration_err)}")
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 4 -> VERSION 5 (Meter Rollover Override Columns) ---
    if current_version == 4:
        logger.info("Upgrading database schema: Version 4 -> Version 5 (Meter Rollover)...")
        try:
            conn.execute("BEGIN TRANSACTION")
            
            # Enforce existence of daily_summary base table before altering
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                date TEXT PRIMARY KEY,
                total_hsd_liters REAL DEFAULT 0.0,
                total_ms_liters REAL DEFAULT 0.0,
                total_cash_calculated REAL DEFAULT 0.0,
                total_credit_sales REAL DEFAULT 0.0,
                total_testing_deductions REAL DEFAULT 0.0,
                is_verified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            cursor.execute("PRAGMA table_info(daily_summary)")
            existing_cols = {c[1] for c in cursor.fetchall()}
            
            if "meter_replaced" not in existing_cols:
                logger.info("Altering table 'daily_summary' to add 'meter_replaced'...")
                cursor.execute("ALTER TABLE daily_summary ADD COLUMN meter_replaced INTEGER DEFAULT 0;")
                
            if "replacement_offset_liters" not in existing_cols:
                logger.info("Altering table 'daily_summary' to add 'replacement_offset_liters'...")
                cursor.execute("ALTER TABLE daily_summary ADD COLUMN replacement_offset_liters REAL DEFAULT 0.0;")
            
            # Increment the version
            cursor.execute("UPDATE sys_version SET value = 5 WHERE key = 'version'")
            
            conn.commit()
            current_version = 5
            logger.info("✓ Upgrade successful: Database schema upgraded cleanly to Version 5.")
        except Exception as migration_err:
            conn.rollback()
            logger.error(f"❌ Migration 4 -> 5 failed midway. Transaction rolled back safely. Error: {str(migration_err)}")
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 5 -> VERSION 6 (Expense Accounting Head Column) ---
    if current_version == 5:
        logger.info("Upgrading database schema: Version 5 -> Version 6 (Expense Accounting Head)...")
        try:
            conn.execute("BEGIN TRANSACTION")

            cursor.execute("PRAGMA table_info(ledger_entries)")
            existing_cols = {c[1] for c in cursor.fetchall()}

            if "accounting_head" not in existing_cols:
                logger.info("Altering table 'ledger_entries' to add 'accounting_head'...")
                cursor.execute(
                    "ALTER TABLE ledger_entries ADD COLUMN accounting_head TEXT DEFAULT NULL;"
                )

            # Increment version
            cursor.execute("UPDATE sys_version SET value = 6 WHERE key = 'version'")

            conn.commit()
            current_version = 6
            logger.info(
                "✓ Upgrade successful: Database schema upgraded cleanly to Version 6 "
                "(Expense Accounting Head)."
            )
        except Exception as migration_err:
            conn.rollback()
            logger.error(
                f"❌ Migration 5 -> 6 failed midway. Transaction rolled back safely. "
                f"Error: {str(migration_err)}"
            )
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 6 -> VERSION 7 (Fuel Purchase Cost Log Table) ---
    if current_version == 6:
        logger.info("Upgrading database schema: Version 6 -> Version 7 (Fuel Purchase Cost Log Table)...")
        try:
            conn.execute("BEGIN TRANSACTION")

            logger.info("Creating 'purchase_cost_log' table...")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_cost_log (
                effective_date TEXT,
                product_type TEXT CHECK(product_type IN ('HSD', 'MS')),
                purchase_rate_per_liter REAL NOT NULL,
                invoice_reference TEXT,
                PRIMARY KEY (effective_date, product_type)
            )
            """)

            # Increment version
            cursor.execute("UPDATE sys_version SET value = 7 WHERE key = 'version'")

            conn.commit()
            current_version = 7
            logger.info(
                "✓ Upgrade successful: Database schema upgraded cleanly to Version 7 "
                "(Fuel Purchase Cost Log Table)."
            )
        except Exception as migration_err:
            conn.rollback()
            logger.error(
                f"❌ Migration 6 -> 7 failed midway. Transaction rolled back safely. "
                f"Error: {str(migration_err)}"
            )
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 7 -> VERSION 8 (Profit Calculation Columns in daily_summary) ---
    if current_version == 7:
        logger.info("Upgrading database schema: Version 7 -> Version 8 (Profit Calculation Columns)...")
        try:
            conn.execute("BEGIN TRANSACTION")

            cursor.execute("PRAGMA table_info(daily_summary)")
            existing_cols = {c[1] for c in cursor.fetchall()}

            if "gross_spread_usd" not in existing_cols:
                logger.info("Altering table 'daily_summary' to add 'gross_spread_usd'...")
                cursor.execute(
                    "ALTER TABLE daily_summary ADD COLUMN gross_spread_usd REAL DEFAULT 0.0;"
                )

            if "realized_profit" not in existing_cols:
                logger.info("Altering table 'daily_summary' to add 'realized_profit'...")
                cursor.execute(
                    "ALTER TABLE daily_summary ADD COLUMN realized_profit REAL DEFAULT 0.0;"
                )

            # Increment version
            cursor.execute("UPDATE sys_version SET value = 8 WHERE key = 'version'")

            conn.commit()
            current_version = 8
            logger.info(
                "✓ Upgrade successful: Database schema upgraded cleanly to Version 8 "
                "(Profit Calculation Columns)."
            )
        except Exception as migration_err:
            conn.rollback()
            logger.error(
                f"❌ Migration 7 -> 8 failed midway. Transaction rolled back safely. "
                f"Error: {str(migration_err)}"
            )
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 8 -> VERSION 9 (Fixed Overhead Matrix Configuration) ---
    if current_version == 8:
        logger.info("Upgrading database schema: Version 8 -> Version 9 (Fixed Overhead Configuration)...")
        try:
            conn.execute("BEGIN TRANSACTION")

            logger.info("Creating 'fixed_overhead_config' table...")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS fixed_overhead_config (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL
            )
            """)

            # Seed default Target Overhead of ₹1,500,000 / month
            cursor.execute("""
            INSERT OR IGNORE INTO fixed_overhead_config (key, value)
            VALUES ('monthly_overhead', 1500000.0)
            """)

            # Increment version
            cursor.execute("UPDATE sys_version SET value = 9 WHERE key = 'version'")

            conn.commit()
            current_version = 9
            logger.info(
                "✓ Upgrade successful: Database schema upgraded cleanly to Version 9 "
                "(Fixed Overhead Configuration)."
            )
        except Exception as migration_err:
            conn.rollback()
            logger.error(
                f"❌ Migration 8 -> 9 failed midway. Transaction rolled back safely. "
                f"Error: {str(migration_err)}"
            )
            conn.close()
            raise migration_err

    # --- MIGRATION: VERSION 9 -> VERSION 10 (Credit Threshold Configuration Table) ---
    if current_version == 9:
        logger.info("Upgrading database schema: Version 9 -> Version 10 (Credit Thresholds Table)...")
        try:
            conn.execute("BEGIN TRANSACTION")

            logger.info("Creating 'credit_thresholds' table...")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS credit_thresholds (
                party_name TEXT PRIMARY KEY,
                max_allowed_credit REAL NOT NULL,
                hard_block_status INTEGER DEFAULT 0
            )
            """)

            # Increment version
            cursor.execute("UPDATE sys_version SET value = 10 WHERE key = 'version'")

            conn.commit()
            current_version = 10
            logger.info(
                "✓ Upgrade successful: Database schema upgraded cleanly to Version 10 "
                "(Credit Thresholds Table)."
            )
        except Exception as migration_err:
            conn.rollback()
            logger.error(
                f"❌ Migration 9 -> 10 failed midway. Transaction rolled back safely. "
                f"Error: {str(migration_err)}"
            )
            conn.close()
            raise migration_err

    conn.close()
    logger.info("Database schema migration sequence completed successfully!")

