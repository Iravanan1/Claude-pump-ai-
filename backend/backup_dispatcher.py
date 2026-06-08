#!/usr/bin/env python3
"""
PumpAI Automated Document and Database Backup Dispatcher Utility
================================================================
Dispatches daily ledger workbook updates via SMTP and Telegram.
Handles transient network failures by caching pending tasks in an
SQLite backup queue to retry during subsequent app boots.
"""

import os
import sqlite3
import logging
import threading
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from dotenv import load_dotenv

# Use requests for Telegram Bot API calls
import requests

# Try importing the common logger
try:
    from logger import logger
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("backup_dispatcher")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

# Load environment configuration
root_dir = Path(__file__).resolve().parent.parent
root_env = root_dir / ".env"
if root_env.exists():
    load_dotenv(dotenv_path=root_env)
else:
    load_dotenv()

def init_queue_db(db_path: str = DB_PATH):
    """
    Ensures that the backup queue table exists in the database.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_queue (
            date_string TEXT PRIMARY KEY,
            retry_count INTEGER DEFAULT 0,
            last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'PENDING'
        );
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to initialize backup queue database table: {str(e)}")

def queue_backup(date_str: str, db_path: str = DB_PATH):
    """
    Registers a backup dispatch task in the queue.
    """
    init_queue_db(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO backup_queue (date_string, status, last_attempt)
            VALUES (?, 'PENDING', CURRENT_TIMESTAMP)
        """, (date_str,))
        conn.commit()
        conn.close()
        logger.info(f"Queued backup dispatcher task for date: {date_str}")
    except Exception as e:
        logger.error(f"Failed to queue backup task for date {date_str}: {str(e)}")

def fetch_metrics_summary(date_str: str, db_path: str = DB_PATH) -> str:
    """
    Queries daily_summary and daily_ledger to build a text summary.
    """
    summary_msg = f"Daily Ledger Summary for {date_str}:\n"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Query daily_summary
        cursor.execute(
            "SELECT total_hsd_liters, total_ms_liters, total_cash_calculated, total_credit_sales, total_testing_deductions, is_verified FROM daily_summary WHERE date = ?",
            (date_str,)
        )
        summary_row = cursor.fetchone()
        
        # Query daily_ledger
        cursor.execute(
            "SELECT total_sales_liters, total_amount_inr, cash_tender, upi_tender, card_tender, udhaar_sales, expenses_amount, validation_status FROM daily_ledger WHERE date = ?",
            (date_str,)
        )
        ledger_row = cursor.fetchone()
        conn.close()
        
        found_any = False
        if summary_row:
            found_any = True
            hsd, ms, cash, credit, testing, verified = summary_row
            status_str = "VERIFIED" if verified == 1 else "PENDING_REVIEW"
            summary_msg += f"- Fuel HSD Sales: {hsd:.2f} L\n"
            summary_msg += f"- Fuel MS Sales: {ms:.2f} L\n"
            summary_msg += f"- Calculated Cash: {cash:.2f} INR\n"
            summary_msg += f"- Credit Sales: {credit:.2f} INR\n"
            summary_msg += f"- Testing Deductions: {testing:.2f} L\n"
            summary_msg += f"- Verification Status: {status_str}\n"
            
        if ledger_row:
            found_any = True
            tot_sales, tot_amt, cash_t, upi_t, card_t, udhaar, exp_amt, val_status = ledger_row
            summary_msg += f"- Total Sales Volume: {tot_sales:.2f} L\n"
            summary_msg += f"- Total Revenue: {tot_amt:.2f} INR\n"
            summary_msg += f"- Cash Tender: {cash_t:.2f} INR\n"
            summary_msg += f"- Expenses: {exp_amt:.2f} INR\n"
            summary_msg += f"- Validation Status: {val_status}\n"
            
        if not found_any:
            summary_msg += "No daily ledger record or summary found in the database."
            
    except Exception as e:
        summary_msg += f"(Error retrieving metrics summary: {str(e)})"
    return summary_msg

def dispatch_daily_ledger_backup(target_date_string: str, db_path: str = DB_PATH) -> bool:
    """
    Locates the master Excel workbook, builds a daily metrics summary,
    and dispatches them via Email and Telegram. Handles failures quietly
    by caching the task in the SQLite backup queue.
    """
    logger.info(f"Dispatching daily ledger backup for date: {target_date_string}...")
    init_queue_db(db_path)
    
    # 1. Locate the excel sheet
    export_path = os.getenv("EXPORT_EXCEL_PATH")
    if not export_path:
        excel_path = os.path.join(WORKSPACE_DIR, "pump_exports", "Pump_Accounts.xlsx")
    else:
        if not os.path.isabs(export_path):
            excel_path = os.path.abspath(os.path.join(WORKSPACE_DIR, export_path))
        else:
            excel_path = export_path
            
    if not os.path.exists(excel_path):
        logger.warning(f"Master spreadsheet workbook not found at: {excel_path}. Dispatch aborted.")
        return False

    # Queue the task first
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO backup_queue (date_string, status) VALUES (?, 'PENDING')", (target_date_string,))
        conn.commit()
        conn.close()
    except Exception as queue_err:
        logger.warning(f"Could not record start of dispatch in queue: {str(queue_err)}")

    log_path = os.path.join(BACKEND_DIR, "logs", "pipeline.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    try:
        # Pull configuration parameters
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = os.getenv("SMTP_PORT")
        sender_email = os.getenv("SENDER_EMAIL")
        receiver_email = os.getenv("RECEIVER_EMAIL")
        
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        # Build message body
        metrics_summary = fetch_metrics_summary(target_date_string, db_path)
        
        email_sent = False
        telegram_sent = False
        
        # --- Email Delivery ---
        if smtp_server and smtp_port and sender_email and receiver_email:
            logger.info("Initializing SMTP email delivery sequence...")
            msg = MIMEMultipart()
            msg["From"] = sender_email
            msg["To"] = receiver_email
            msg["Subject"] = f"PumpAI Daily Ledger Backup - {target_date_string}"
            
            msg.attach(MIMEText(metrics_summary, "plain"))
            
            with open(excel_path, "rb") as f:
                attachment = MIMEApplication(f.read(), _subtype="xlsx")
                attachment.add_header("Content-Disposition", "attachment", filename=os.path.basename(excel_path))
                msg.attach(attachment)
                
            port = int(smtp_port)
            if port == 465:
                server = smtplib.SMTP_SSL(smtp_server, port, timeout=10)
            else:
                server = smtplib.SMTP(smtp_server, port, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
                
            smtp_password = os.getenv("SMTP_PASSWORD")
            if smtp_password:
                server.login(sender_email, smtp_password)
                
            server.send_message(msg)
            server.quit()
            logger.info("Email delivery successfully transmitted to inbox!")
            email_sent = True
        else:
            logger.info("Email parameters not configured in .env. Skipping email dispatch.")
            email_sent = True # Treat as success if not configured
            
        # --- Telegram Delivery ---
        if telegram_token and telegram_chat_id:
            logger.info("Initializing Telegram gateway delivery sequence...")
            url = f"https://api.telegram.org/bot{telegram_token}/sendDocument"
            
            with open(excel_path, "rb") as f:
                files = {"document": (os.path.basename(excel_path), f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
                data = {
                    "chat_id": telegram_chat_id,
                    "caption": f"PumpAI Daily Ledger Summary - {target_date_string}\n\n{metrics_summary}"
                }
                response = requests.post(url, data=data, files=files, timeout=15)
                response.raise_for_status()
                
            logger.info("Telegram document drop completed successfully!")
            telegram_sent = True
        else:
            logger.info("Telegram configuration parameters not found or incomplete. Skipping Telegram dispatch.")
            telegram_sent = True # Treat as success if not configured

        # If both dispatches succeeded, resolve the queue item
        if email_sent and telegram_sent:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM backup_queue WHERE date_string = ?", (target_date_string,))
            conn.commit()
            conn.close()
            logger.info(f"Backup dispatcher task resolved successfully for date: {target_date_string}")
            return True
            
        return False

    except Exception as e:
        # Quiet Exception Control: suppress error, write to pipeline.log, and update queue status to PENDING
        warning_msg = f"[{datetime.now().isoformat()}] Backup dispatch failed for date {target_date_string}: {str(e)}\n"
        try:
            with open(log_path, "a") as lf:
                lf.write(warning_msg)
        except Exception:
            pass
            
        logger.warning(f"Quiet connection / connection protocol error trapped: {str(e)}. Caching task in queue.")
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE backup_queue 
                SET retry_count = retry_count + 1, status = 'PENDING', last_attempt = CURRENT_TIMESTAMP
                WHERE date_string = ?
            """, (target_date_string,))
            conn.commit()
            conn.close()
        except Exception as queue_update_err:
            logger.error(f"Failed to update queue states: {str(queue_update_err)}")
            
        return False

def dispatch_daily_ledger_backup_background(target_date_string: str, db_path: str = DB_PATH) -> threading.Thread:
    """
    Spawns dispatch_daily_ledger_backup inside a background daemon thread.
    """
    t = threading.Thread(target=dispatch_daily_ledger_backup, args=(target_date_string, db_path), daemon=True)
    t.start()
    return t

def retry_pending_backups(db_path: str = DB_PATH):
    """
    Queries all PENDING backup tasks and dispatches them sequentially.
    Called on system boot.
    """
    init_queue_db(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT date_string FROM backup_queue WHERE status = 'PENDING'")
        pending_dates = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if pending_dates:
            logger.info(f"Found {len(pending_dates)} pending backups to retry on boot sequence.")
            for date_str in pending_dates:
                # Dispatch synchronously inside this retry thread context
                dispatch_daily_ledger_backup(date_str, db_path)
        else:
            logger.info("No pending backup dispatch actions detected in queue.")
    except Exception as e:
        logger.error(f"Failed executing retry boot sequence: {str(e)}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PumpAI Backup Dispatcher Command Line Tool.")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Target date YYYY-MM-DD.")
    parser.add_argument("--db", type=str, default=DB_PATH, help="Path to SQLite database.")
    args = parser.parse_args()
    
    dispatch_daily_ledger_backup(args.date, args.db)
