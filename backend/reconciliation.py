import os
import sqlite3
from logger import logger

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_recon_db(db_path=DB_PATH):
    """
    Initializes the stock_recon table in SQLite database.
    """
    logger.info(f"Initializing stock_recon table in SQLite database at {os.path.abspath(db_path)}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_recon (
            date TEXT PRIMARY KEY,
            hsd_opening_dip_liters REAL DEFAULT 0.0,
            hsd_receipt_liters REAL DEFAULT 0.0,
            hsd_closing_dip_liters REAL DEFAULT 0.0,
            ms_opening_dip_liters REAL DEFAULT 0.0,
            ms_receipt_liters REAL DEFAULT 0.0,
            ms_closing_dip_liters REAL DEFAULT 0.0,
            actual_cash_deposited REAL DEFAULT 0.0,
            digital_wallet_settlements REAL DEFAULT 0.0,
            logged_udhaar_entries REAL DEFAULT 0.0
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'stock_recon' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'stock_recon' table: {str(e)}")
        raise e

def save_reconciliation(
    date_str: str,
    hsd_opening: float,
    hsd_receipt: float,
    hsd_closing: float,
    ms_opening: float,
    ms_receipt: float,
    ms_closing: float,
    actual_cash: float = 0.0,
    digital_settlements: float = 0.0,
    udhaar_entries: float = 0.0,
    db_path=DB_PATH
):
    """
    Saves or updates daily dip values and cash collection overrides in the stock_recon table.
    """
    logger.info(f"Saving stock reconciliation entries for date {date_str}...")
    try:
        from tank_calibration import convert_dip_to_liters
        hsd_opening_liters = convert_dip_to_liters('Tank_1_HSD', hsd_opening, db_path=db_path)
        hsd_closing_liters = convert_dip_to_liters('Tank_1_HSD', hsd_closing, db_path=db_path)
        ms_opening_liters = convert_dip_to_liters('Tank_2_MS', ms_opening, db_path=db_path)
        ms_closing_liters = convert_dip_to_liters('Tank_2_MS', ms_closing, db_path=db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO stock_recon (
                date,
                hsd_opening_dip_liters,
                hsd_receipt_liters,
                hsd_closing_dip_liters,
                ms_opening_dip_liters,
                ms_receipt_liters,
                ms_closing_dip_liters,
                actual_cash_deposited,
                digital_wallet_settlements,
                logged_udhaar_entries
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str,
            hsd_opening_liters,
            hsd_receipt,
            hsd_closing_liters,
            ms_opening_liters,
            ms_receipt,
            ms_closing_liters,
            actual_cash,
            digital_settlements,
            udhaar_entries
        ))
        conn.commit()
        conn.close()
        logger.info(f"Stock reconciliation for date {date_str} saved successfully.")

        # Double-logging Hook: Automatically call record_dip_reading from dip_profiler.py
        try:
            from dip_profiler import record_dip_reading, init_dip_profiler_db
            init_dip_profiler_db(db_path=db_path)
            
            # Recalculate daily variances to get the true variance for these readings
            calc_vars = calculate_daily_variance(date_str, db_path=db_path)
            hsd_var = calc_vars.get("hsd_variance_liters", 0.0)
            ms_var = calc_vars.get("ms_variance_liters", 0.0)
            meter_ok = (calc_vars.get("cash_status") == "balanced")
            
            record_dip_reading(
                tank_id='Tank_1_HSD',
                reading_date=date_str,
                dip_mm=hsd_closing,
                actual_variance_L=hsd_var,
                meter_check_ok=meter_ok,
                source='auto',
                db_path=db_path
            )
            
            record_dip_reading(
                tank_id='Tank_2_MS',
                reading_date=date_str,
                dip_mm=ms_closing,
                actual_variance_L=ms_var,
                meter_check_ok=meter_ok,
                source='auto',
                db_path=db_path
            )
            logger.info(f"Double-logged raw millimeter evening dip readings to tank_dip_log for date {date_str}.")
        except Exception as dip_err:
            logger.warning(f"Failed to double-log to tank_dip_log: {dip_err}")

    except Exception as e:
        logger.error(f"Failed to save stock reconciliation for date {date_str}: {str(e)}")
        raise e

def get_reconciliation(date_str: str, db_path=DB_PATH) -> dict:
    """
    Retrieves the raw stock reconciliation entries for a given date.
    Returns a dictionary of raw values, or all 0.0 values if not found.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                hsd_opening_dip_liters, hsd_receipt_liters, hsd_closing_dip_liters,
                ms_opening_dip_liters, ms_receipt_liters, ms_closing_dip_liters,
                actual_cash_deposited, digital_wallet_settlements, logged_udhaar_entries
            FROM stock_recon WHERE date = ?
        """, (date_str,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "date": date_str,
                "hsd_opening_dip_liters": row[0],
                "hsd_receipt_liters": row[1],
                "hsd_closing_dip_liters": row[2],
                "ms_opening_dip_liters": row[3],
                "ms_receipt_liters": row[4],
                "ms_closing_dip_liters": row[5],
                "actual_cash_deposited": row[6],
                "digital_wallet_settlements": row[7],
                "logged_udhaar_entries": row[8]
            }
        else:
            return {
                "date": date_str,
                "hsd_opening_dip_liters": 0.0,
                "hsd_receipt_liters": 0.0,
                "hsd_closing_dip_liters": 0.0,
                "ms_opening_dip_liters": 0.0,
                "ms_receipt_liters": 0.0,
                "ms_closing_dip_liters": 0.0,
                "actual_cash_deposited": 0.0,
                "digital_wallet_settlements": 0.0,
                "logged_udhaar_entries": 0.0
            }
    except Exception as e:
        logger.error(f"Failed to query stock reconciliation for date {date_str}: {str(e)}")
        return {
            "date": date_str,
            "hsd_opening_dip_liters": 0.0,
            "hsd_receipt_liters": 0.0,
            "hsd_closing_dip_liters": 0.0,
            "ms_opening_dip_liters": 0.0,
            "ms_receipt_liters": 0.0,
            "ms_closing_dip_liters": 0.0,
            "actual_cash_deposited": 0.0,
            "digital_wallet_settlements": 0.0,
            "logged_udhaar_entries": 0.0
        }

def calculate_daily_variance(date_string: str, db_path=DB_PATH) -> dict:
    """
    Computes expected book stock, actual closing stock, variances,
    and cash short/over reconciliation details.
    
    Equations:
      - Expected Book Stock = Opening Dip + Tank Receipts - Totalizer Meter Sales
      - Operational Variance = Actual Closing Dip - Expected Book Stock
      - Cash Short/Over = (Actual Cash + Digital Settlements + Udhaar Entries) - Calculated Sales Value
    """
    logger.info(f"Running daily operational and cash reconciliation calculations for date: {date_string}...")
    try:
        # 1. Fetch stock reconciliation dip entries
        recon = get_reconciliation(date_string, db_path=db_path)
        
        # 2. Query totalizer sales and calculated sales value from daily_summary / daily_ledger
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Query daily summary
        cursor.execute("PRAGMA table_info(daily_summary)")
        summary_cols = [c[1] for c in cursor.fetchall()]
        has_extended = "total_regular_hsd_liters" in summary_cols
        
        if has_extended:
            cursor.execute("""
                SELECT total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales,
                       total_regular_hsd_liters, total_premium_hsd_liters, 
                       total_regular_ms_liters, total_premium_ms_liters
                FROM daily_summary WHERE date = ?
            """, (date_string,))
        else:
            cursor.execute("""
                SELECT total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales
                FROM daily_summary WHERE date = ?
            """, (date_string,))
        summary_row = cursor.fetchone()
        
        # Query daily ledger for actual cash/digital wallet tenders if not overridden
        cursor.execute("""
            SELECT cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales
            FROM daily_ledger WHERE date = ?
        """, (date_string,))
        ledger_row = cursor.fetchone()
        
        # Query internal consumption draws
        hsd_internal = 0.0
        ms_internal = 0.0
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='internal_consumption'")
            if cursor.fetchone():
                cursor.execute("""
                    SELECT product_type, SUM(liters_drawn)
                    FROM internal_consumption
                    WHERE date = ?
                    GROUP BY product_type
                """, (date_string,))
                for row_p in cursor.fetchall():
                    if row_p[0] == 'HSD':
                        hsd_internal = float(row_p[1] or 0.0)
                    elif row_p[0] == 'MS':
                        ms_internal = float(row_p[1] or 0.0)
        except Exception as err:
            logger.warning(f"Failed to query internal consumption for reconciliation: {err}")
            
        # Query nozzle testing logs
        hsd_testing = 0.0
        ms_testing = 0.0
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nozzle_testing_logs'")
            if cursor.fetchone():
                cursor.execute("""
                    SELECT product_type, SUM(testing_volume_liters)
                    FROM nozzle_testing_logs
                    WHERE date = ? AND rts_verified = 1
                    GROUP BY product_type
                """, (date_string,))
                for row_t in cursor.fetchall():
                    if row_t[0] == 'HSD':
                        hsd_testing = float(row_t[1] or 0.0)
                    elif row_t[0] == 'MS':
                        ms_testing = float(row_t[1] or 0.0)
        except Exception as err:
            logger.warning(f"Failed to query nozzle testing logs for reconciliation: {err}")
            
        conn.close()
        
        # Parse totalizer meter sales
        total_hsd_sales = summary_row[0] if summary_row else 0.0
        total_ms_sales = summary_row[1] if summary_row else 0.0
        db_calculated_sales_value = summary_row[2] if summary_row else 0.0
        total_credit_sales = summary_row[3] if summary_row else 0.0
        
        if summary_row and len(summary_row) > 4:
            total_reg_hsd = float(summary_row[4] or 0.0)
            total_prem_hsd = float(summary_row[5] or 0.0)
            total_reg_ms = float(summary_row[6] or 0.0)
            total_prem_ms = float(summary_row[7] or 0.0)
        else:
            total_reg_hsd = total_hsd_sales
            total_prem_hsd = 0.0
            total_reg_ms = total_ms_sales
            total_prem_ms = 0.0
            
        # Fetch rates from price registry to perform precise billable calculations
        from price_registry import get_rates_for_date
        from premium_products import resolve_variant_rate
        rates = get_rates_for_date(date_string)
        
        reg_hsd_rate = resolve_variant_rate(rates, "REGULAR_HSD")
        prem_hsd_rate = resolve_variant_rate(rates, "PREMIUM_HSD")
        reg_ms_rate = resolve_variant_rate(rates, "REGULAR_MS")
        prem_ms_rate = resolve_variant_rate(rates, "PREMIUM_MS")
            
        # Calculate billable liters and expected revenue
        reg_hsd_billable = max(0.0, total_reg_hsd - hsd_testing)
        prem_hsd_billable = max(0.0, total_prem_hsd)
        reg_ms_billable = max(0.0, total_reg_ms - ms_testing)
        prem_ms_billable = max(0.0, total_prem_ms)
        
        hsd_billable_liters = reg_hsd_billable + prem_hsd_billable
        ms_billable_liters = reg_ms_billable + prem_ms_billable
        
        expected_hsd_revenue = (reg_hsd_billable * reg_hsd_rate) + (prem_hsd_billable * prem_hsd_rate)
        expected_ms_revenue = (reg_ms_billable * reg_ms_rate) + (prem_ms_billable * prem_ms_rate)
        expected_billable_revenue = expected_hsd_revenue + expected_ms_revenue
        
        # If there is active nozzle testing, intercept the volume-to-cash reconciliation; else fallback to original
        if hsd_testing > 0 or ms_testing > 0:
            calculated_sales_value = expected_billable_revenue
        else:
            calculated_sales_value = db_calculated_sales_value
        
        # Determine defaults from saved daily_ledger
        saved_cash = ledger_row[0] if ledger_row else 0.0
        saved_digital = (ledger_row[1] + ledger_row[2] + ledger_row[3]) if ledger_row else 0.0
        saved_udhaar = ledger_row[4] if ledger_row else total_credit_sales
        
        # If stock_recon has non-zero user inputs, prioritize them; else fallback to ledger defaults
        actual_cash = recon["actual_cash_deposited"] if recon["actual_cash_deposited"] > 0 else saved_cash
        digital_settlements = recon["digital_wallet_settlements"] if recon["digital_wallet_settlements"] > 0 else saved_digital
        udhaar_entries = recon["logged_udhaar_entries"] if recon["logged_udhaar_entries"] > 0 else saved_udhaar
        
        # Calculations: HSD (Diesel) - Account for RTS testing volume as inbound stock return (+ hsd_testing)
        expected_hsd_book = recon["hsd_opening_dip_liters"] + recon["hsd_receipt_liters"] - total_hsd_sales - hsd_internal + hsd_testing
        hsd_variance = recon["hsd_closing_dip_liters"] - expected_hsd_book
        
        # Calculations: MS (Petrol) - Account for RTS testing volume as inbound stock return (+ ms_testing)
        expected_ms_book = recon["ms_opening_dip_liters"] + recon["ms_receipt_liters"] - total_ms_sales - ms_internal + ms_testing
        ms_variance = recon["ms_closing_dip_liters"] - expected_ms_book
        
        # Cash Reconciliation
        actual_reconciled_total = actual_cash + digital_settlements + udhaar_entries
        cash_short_or_over = actual_reconciled_total - calculated_sales_value
        
        if abs(cash_short_or_over) < 0.01:
            cash_status = "balanced"
        elif cash_short_or_over < 0:
            cash_status = "shortage"
        else:
            cash_status = "overage"
            
        # Query DSM shifts for comparative variance analysis
        dsm_list = []
        total_dsm_cash = 0.0
        total_dsm_digital = 0.0
        total_dsm_shortage_or_excess = 0.0
        
        try:
            conn_dsm = sqlite3.connect(db_path)
            cursor_dsm = conn_dsm.cursor()
            cursor_dsm.execute("""
                SELECT dsm_name, shift_type, assigned_nozzles, cash_handed_over, 
                       digital_slips_value, calculated_shortage_or_excess
                FROM dsm_shifts WHERE date = ?
            """, (date_string,))
            dsm_rows = cursor_dsm.fetchall()
            conn_dsm.close()
            
            for row in dsm_rows:
                dsm_name, shift_type, assigned_nozzles, cash_handed, digital_slips, short_excess = row
                total_dsm_cash += cash_handed
                total_dsm_digital += digital_slips
                total_dsm_shortage_or_excess += short_excess
                dsm_list.append({
                    "dsm_name": dsm_name,
                    "shift_type": shift_type,
                    "assigned_nozzles": assigned_nozzles,
                    "cash_handed_over": cash_handed,
                    "digital_slips_value": digital_slips,
                    "calculated_shortage_or_excess": short_excess
                })
        except Exception as dsm_err:
            logger.warning(f"Failed to query DSM shifts in reconciliation variance analysis: {str(dsm_err)}")
            
        # Perform comparative analysis
        dsm_variance_analysis = ""
        if dsm_list:
            matched_dsm = None
            for d in dsm_list:
                if abs(d["calculated_shortage_or_excess"] - cash_short_or_over) < 0.05:
                    matched_dsm = d
                    break
            
            if matched_dsm:
                action_word = "shortage" if cash_short_or_over < 0 else "excess"
                dsm_variance_analysis = (
                    f"DSM {matched_dsm['dsm_name']} has a {action_word} of "
                    f"₹{abs(matched_dsm['calculated_shortage_or_excess']):.2f} which matches the "
                    f"global pump {cash_status} of ₹{abs(cash_short_or_over):.2f}."
                )
            elif abs(total_dsm_shortage_or_excess - cash_short_or_over) < 0.05:
                dsm_variance_analysis = (
                    f"The sum of all active DSM variances (₹{total_dsm_shortage_or_excess:.2f}) "
                    f"matches the global pump {cash_status} of ₹{cash_short_or_over:.2f}."
                )
            else:
                dsm_variance_analysis = (
                    f"Global pump variance is ₹{cash_short_or_over:.2f}. "
                    f"Active DSM variances total ₹{total_dsm_shortage_or_excess:.2f}."
                )
        else:
            dsm_variance_analysis = "No DSM shift allocations recorded on this date."
            
        return {
            "date": date_string,
            # Inputs
            "hsd_opening_dip_liters": recon["hsd_opening_dip_liters"],
            "hsd_receipt_liters": recon["hsd_receipt_liters"],
            "hsd_closing_dip_liters": recon["hsd_closing_dip_liters"],
            "ms_opening_dip_liters": recon["ms_opening_dip_liters"],
            "ms_receipt_liters": recon["ms_receipt_liters"],
            "ms_closing_dip_liters": recon["ms_closing_dip_liters"],
            "actual_cash_deposited": actual_cash,
            "digital_wallet_settlements": digital_settlements,
            "logged_udhaar_entries": udhaar_entries,
            
            # DB values for references
            "total_hsd_meter_sales": total_hsd_sales,
            "total_ms_meter_sales": total_ms_sales,
            "calculated_sales_value": calculated_sales_value,
            
            # Outputs
            "expected_hsd_book_stock": expected_hsd_book,
            "hsd_variance_liters": hsd_variance,
            "expected_ms_book_stock": expected_ms_book,
            "ms_variance_liters": ms_variance,
            "actual_reconciled_total": actual_reconciled_total,
            "cash_short_or_over": cash_short_or_over,
            "cash_status": cash_status,
            
            # DSM details
            "dsm_shifts": dsm_list,
            "total_dsm_cash_handed_over": total_dsm_cash,
            "total_dsm_digital_slips": total_dsm_digital,
            "total_dsm_shortage_or_excess": total_dsm_shortage_or_excess,
            "dsm_variance_analysis": dsm_variance_analysis,
            "hsd_internal_consumption": hsd_internal,
            "ms_internal_consumption": ms_internal,
            "hsd_testing_volume": hsd_testing,
            "ms_testing_volume": ms_testing,
            "hsd_billable_liters": hsd_billable_liters,
            "ms_billable_liters": ms_billable_liters,
            "expected_hsd_revenue": expected_hsd_revenue,
            "expected_ms_revenue": expected_ms_revenue
        }
    except Exception as e:
        logger.error(f"Reconciliation calculation failed for date {date_string}: {str(e)}")
        raise e

# Run schema setup automatically on load
init_recon_db()
