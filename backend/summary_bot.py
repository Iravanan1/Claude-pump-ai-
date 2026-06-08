"""
Daily Summary Text Compiler Module for WhatsApp/SMS drafts.
"""

import os
import sqlite3
from typing import Dict, Any
from logger import logger
from crypto_vault import decrypt_field
from reconciliation import calculate_daily_variance

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def compile_whatsapp_sms_draft(date_string: str, db_path: str = DB_PATH) -> str:
    """
    Queries local database for a finalized day's entry and structures a clean,
    scannable, text-only operational digest for WhatsApp / SMS.
    """
    logger.info(f"Compiling WhatsApp/SMS daily summary draft for date: {date_string}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Query daily_summary for HSD sold, MS sold, calculated cash, credit sales
        cursor.execute("""
            SELECT total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales
            FROM daily_summary WHERE date = ?
        """, (date_string.strip(),))
        summary_row = cursor.fetchone()
        
        # 2. Query daily_ledger for tenders
        cursor.execute("""
            SELECT cash_tender, upi_tender, paytm_transfers, card_tender, udhaar_sales, raw_data
            FROM daily_ledger WHERE date = ?
        """, (date_string.strip(),))
        ledger_row = cursor.fetchone()
        
        # 3. Query ledger_entries for credit sales
        cursor.execute("""
            SELECT party_name, amount FROM ledger_entries 
            WHERE date = ? AND type = 'udhaar'
        """, (date_string.strip(),))
        credit_rows = cursor.fetchall()
        
        # 4. Check if stock_recon table has entries for actual cash, digital, and udhaar
        cursor.execute("""
            SELECT actual_cash_deposited, digital_wallet_settlements, logged_udhaar_entries
            FROM stock_recon WHERE date = ?
        """, (date_string.strip(),))
        recon_row = cursor.fetchone()
        conn.close()
        
        # Decrypt credit sales and sort descending by amount
        credit_parties = []
        for r in credit_rows:
            try:
                p_name = decrypt_field(r[0], return_type=str)
                amt = decrypt_field(r[1], return_type=float)
                credit_parties.append((p_name, amt))
            except Exception as decrypt_err:
                logger.warning(f"Failed to decrypt credit entry: {str(decrypt_err)}")
                credit_parties.append((str(r[0]), float(r[1] or 0.0)))
                
        # Sort credit parties descending by amount
        credit_parties.sort(key=lambda x: x[1], reverse=True)
        
        # Extract values
        hsd_sold = 0.0
        ms_sold = 0.0
        total_cash_expected = 0.0
        total_credit_sales = 0.0
        
        if summary_row:
            hsd_sold = float(summary_row[0] or 0.0)
            ms_sold = float(summary_row[1] or 0.0)
            total_cash_expected = float(summary_row[2] or 0.0)
            total_credit_sales = float(summary_row[3] or 0.0)
            
        cash_tender = 0.0
        upi_tender = 0.0
        paytm_transfers = 0.0
        card_tender = 0.0
        udhaar_sales = total_credit_sales
        
        if ledger_row:
            cash_tender = float(ledger_row[0] or 0.0)
            upi_tender = float(ledger_row[1] or 0.0)
            paytm_transfers = float(ledger_row[2] or 0.0)
            card_tender = float(ledger_row[3] or 0.0)
            udhaar_sales = float(ledger_row[4] or 0.0)
            
        # Overrides from stock_recon if they exist
        actual_cash = 0.0
        digital_settlements = 0.0
        udhaar_entries = 0.0
        
        if recon_row:
            actual_cash = float(recon_row[0] or 0.0)
            digital_settlements = float(recon_row[1] or 0.0)
            udhaar_entries = float(recon_row[2] or 0.0)
            
        # Resolve values for output
        # Total Cash Collected: either actual_cash (if > 0) or cash_tender
        total_cash_val = actual_cash if actual_cash > 0 else cash_tender
        
        # Digital Drops: either digital_settlements (if > 0) or upi + paytm + card
        digital_drops_val = digital_settlements if digital_settlements > 0 else (upi_tender + paytm_transfers + card_tender)
        
        # Total Credit Sales: either udhaar_entries (if > 0) or udhaar_sales
        credit_sales_val = udhaar_entries if udhaar_entries > 0 else udhaar_sales
        
        # Shortages/Variance:
        # Use calculate_daily_variance to find exact cash_short_or_over
        variance_val = 0.0
        try:
            recon_calculations = calculate_daily_variance(date_string, db_path=db_path)
            variance_val = recon_calculations.get("cash_short_or_over", 0.0)
        except Exception as recon_err:
            logger.warning(f"Reconciliation math failed, fallback to raw ledger math: {str(recon_err)}")
            # Fallback math
            actual_total = total_cash_val + digital_drops_val + credit_sales_val
            variance_val = actual_total - total_cash_expected
            
        # Format major credit parties list
        if credit_parties:
            major_parties_str = ", ".join([f"{name}: ₹{int(amt) if amt.is_integer() else amt:g}" for name, amt in credit_parties])
        else:
            major_parties_str = ""
            
        # Format HSD and MS sold nicely
        hsd_str = f"{int(hsd_sold) if hsd_sold.is_integer() else hsd_sold:g}"
        ms_str = f"{int(ms_sold) if ms_sold.is_integer() else ms_sold:g}"
        
        # Format currency fields to show nice representations
        def format_currency(val: float) -> str:
            if val.is_integer():
                return f"{int(val)}"
            return f"{val:g}"
            
        cash_str = format_currency(total_cash_val)
        digital_str = format_currency(digital_drops_val)
        credit_str = format_currency(credit_sales_val)
        variance_str = format_currency(variance_val)
        
        # Build text block exactly in this business layout:
        # ---
        # *Daily Pump Summary: [Date]*
        # • HSD Sold: [X] Liters | MS Sold: [Y] Liters
        # • Total Cash Collected: ₹[Amount]
        # • Digital Drops (Paytm/Cards): ₹[Amount]
        # • Total Credit Sales (Udhaar): ₹[Amount]
        # • Major Credit Parties: [Party Name 1: ₹Amount, Party Name 2: ₹Amount]
        # • Shortages/Variance: ₹[Amount]
        # ---
        digest = (
            "---\n"
            f"*Daily Pump Summary: {date_string.strip()}*\n"
            f"• HSD Sold: {hsd_str} Liters | MS Sold: {ms_str} Liters\n"
            f"• Total Cash Collected: ₹{cash_str}\n"
            f"• Digital Drops (Paytm/Cards): ₹{digital_str}\n"
            f"• Total Credit Sales (Udhaar): ₹{credit_str}\n"
            f"• Major Credit Parties: [{major_parties_str}]\n"
            f"• Shortages/Variance: ₹{variance_str}\n"
            "---"
        )
        return digest
    except Exception as e:
        logger.error(f"Failed to compile daily summary text: {str(e)}")
        # Clean fallback in exact template
        return (
            "---\n"
            f"*Daily Pump Summary: {date_string}*\n"
            "• HSD Sold: 0 Liters | MS Sold: 0 Liters\n"
            "• Total Cash Collected: ₹0\n"
            "• Digital Drops (Paytm/Cards): ₹0\n"
            "• Total Credit Sales (Udhaar): ₹0\n"
            "• Major Credit Parties: []\n"
            "• Shortages/Variance: ₹0\n"
            "---"
        )
