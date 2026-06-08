#!/usr/bin/env python3
"""
ESC/POS Thermal Receipt Printer Driver and Slip Layout Compiler.
Compiles 48-column credit slips and dispatches raw bytes to native system print queues.
"""

import os
import sys
import subprocess
import logging
from datetime import datetime
from typing import List, Dict, Any

# Target local OS print queue name
TARGET_PRINTER_QUEUE_NAME = "thermal_pos"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ThermalPrinter")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)
def get_receipts_dir() -> str:
    path = os.path.join(WORKSPACE_DIR, "pump_exports", "thermal_receipts")
    os.makedirs(path, exist_ok=True)
    return path


# ESC/POS Hardware Command Constants
ESC = b'\x1b'
GS = b'\x1d'
LF = b'\n'

INIT_PRINTER = ESC + b'\x40'
ALIGN_LEFT = ESC + b'\x61\x00'
ALIGN_CENTER = ESC + b'\x61\x01'
ALIGN_RIGHT = ESC + b'\x61\x02'

BOLD_ON = ESC + b'\x45\x01'
BOLD_OFF = ESC + b'\x45\x00'

FONT_DOUBLE = GS + b'\x21\x11'  # Double height and double width
FONT_NORMAL = GS + b'\x21\x00'  # Normal size

CUT_PAPER = GS + b'\x56\x01'    # Partial Cut paper

def connect_thermal_printer() -> Dict[str, Any]:
    """
    Auto-discovers local thermal receipt printers via macOS/Linux lpstat printer status.
    First checks the configured TARGET_PRINTER_QUEUE_NAME or environment variables.
    If the specified device queue is unavailable, falls back gracefully.
    """
    logger.info("Scanning for local system thermal printer queues...")
    
    status = {
        "connected": False,
        "printer_name": TARGET_PRINTER_QUEUE_NAME,
        "method": None,
        "message": f"Configured printer queue: {TARGET_PRINTER_QUEUE_NAME}"
    }

    # Configurable environment override to disable subprocess / spooler calls
    if os.environ.get("DISABLE_PRINTER_SUBPROCESS", "false").lower() == "true":
        status["message"] = "Printer subprocess disabled via environment variable fallback."
        return status

    # Verify if lpstat exists, fallback if not
    lpstat_path = None
    for path in ["/usr/bin/lpstat", "/usr/sbin/lpstat", "lpstat"]:
        if path == "lpstat":
            import shutil
            if shutil.which("lpstat"):
                lpstat_path = "lpstat"
                break
        elif os.path.exists(path):
            lpstat_path = path
            break

    if not lpstat_path:
        status["message"] = "Native spooler lpstat utility is not available on this system."
        return status

    try:
        # Query active system printer names with a strict timeout of 3 seconds to prevent hanging
        output = subprocess.check_output([lpstat_path, "-p"], stderr=subprocess.STDOUT, timeout=3).decode("utf-8")
        lines = output.splitlines()
        
        discovered_queues = []
        target_found = False
        target_enabled = False
        
        for line in lines:
            if line.startswith("printer"):
                tokens = line.split()
                if len(tokens) >= 2:
                    p_name = tokens[1]
                    discovered_queues.append(p_name)
                    if p_name == TARGET_PRINTER_QUEUE_NAME:
                        target_found = True
                        if "enabled" in line.lower() or "is idle" in line.lower():
                            target_enabled = True

        if target_found and target_enabled:
            logger.info(f"✓ Discovered active target thermal printer queue: {TARGET_PRINTER_QUEUE_NAME}")
            status["connected"] = True
            status["printer_name"] = TARGET_PRINTER_QUEUE_NAME
            status["method"] = "lp"
            status["message"] = f"Connected successfully to active target queue: {TARGET_PRINTER_QUEUE_NAME}"
            return status
        elif target_found:
            logger.warning(f"⚠ Target printer queue '{TARGET_PRINTER_QUEUE_NAME}' was found but appears offline or disabled.")
            status["message"] = f"Target queue '{TARGET_PRINTER_QUEUE_NAME}' is offline or disabled."
            return status

        # If not found, try to auto-discover other matching names case-insensitively
        target_keywords = ["thermal", "receipt", "pos", "xprinter", "epson"]
        for p_name in discovered_queues:
            if any(kw in p_name.lower() for kw in target_keywords):
                logger.info(f"✓ Auto-discovered alternative active thermal receipt printer: {p_name}")
                status["connected"] = True
                status["printer_name"] = p_name
                status["method"] = "lp"
                status["message"] = f"Connected successfully to receipt printer: {p_name}"
                return status
                
        if discovered_queues:
            first_p = discovered_queues[0]
            logger.info(f"⚠ No specific thermal printer matched. Falling back to default queue: {first_p}")
            status["connected"] = True
            status["printer_name"] = first_p
            status["method"] = "lp"
            status["message"] = f"Using default printer: {first_p}"
            return status

    except subprocess.TimeoutExpired:
        logger.warning("lpstat check timed out. Printer system spooler is hanging.")
        status["message"] = "lpstat check timed out (spooler hanging)."
    except Exception as e:
        logger.warning(f"Failed to query local printer status: {str(e)}")
        status["message"] = f"Spooler status query failed: {str(e)}"

    return status

def format_48col_ascii(party_name: str, transactions: List[Dict[str, Any]], net_due: float) -> str:
    """
    Formats customer ledger details into a clean 48-column ASCII aligned receipt text stream.
    Column Mapping: Date (10) | Vehicle (15) | Spaces (8) | Amount (15) = 48 characters.
    """
    lines = []
    
    # 1. Header Block
    lines.append("=" * 48)
    lines.append("{:^48}".format("PUMPAI FUEL STATION"))
    lines.append("{:^48}".format("Trust & Statutory Integrity"))
    lines.append("{:^48}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("=" * 48)
    
    # 2. Customer details
    lines.append(f"Customer Name: {party_name}")
    lines.append("-" * 48)
    
    # 3. Grid Columns Header
    # Date (10) | Vehicle (15) | Spaces (8) | Amount (15)
    lines.append("{:<10}  {:<15}{:>21}".format("Date", "Vehicle No", "Amount (INR)"))
    lines.append("-" * 48)
    
    # 4. Itemized ledger list
    for tx in transactions:
        tx_date = tx.get("date", "")
        vehicle = tx.get("vehicle_no") or tx.get("vehicle_wheel_no") or "N/A"
        # Truncate vehicle name if too long for spacing
        if len(vehicle) > 15:
            vehicle = vehicle[:12] + "..."
            
        amount = tx.get("amount", 0.0)
        amount_str = f"{amount:,.2f}"
        
        # Format aligned row
        row = "{:<10}  {:<15}{:>21}".format(tx_date, vehicle, amount_str)
        lines.append(row)
        
    lines.append("-" * 48)
    
    # 5. Outstandings Due
    lines.append("{:<20}{:>28}".format("TOTAL OUTSTANDING:", f"INR {net_due:,.2f}"))
    lines.append("=" * 48)
    lines.append("{:^48}".format("Thank you for your business!"))
    lines.append("{:^48}".format("Please settle dues on time."))
    lines.append("=" * 48)
    lines.append("\n\n\n")  # Feed spacing
    
    return "\n".join(lines)

def print_credit_ledger_slip(party_name: str, transactions: List[Dict[str, Any]], net_due: float, dry_run: bool = False) -> Dict[str, Any]:
    """
    Compiles credit transactions and outstanding dues into a styled ESC/POS command stream,
    spools it raw to the detected printer, and writes a dry-run ASCII file to disk.
    If hardware printing is offline or fails, logs slip out to last_printed_slip.txt diagnostics.
    """
    logger.info(f"Compiling credit ledger receipt slip for customer: {party_name}")
    
    # 1. Format clean human-readable ASCII layout
    ascii_slip = format_48col_ascii(party_name, transactions, net_due)
    
    # Fallback diagnostics printer helper
    def write_fallback_diagnostics():
        try:
            log_dir = os.path.join(BACKEND_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            diagnostics_path = os.path.join(log_dir, "last_printed_slip.txt")
            with open(diagnostics_path, "w", encoding="utf-8") as diag_f:
                diag_f.write(ascii_slip)
            logger.info(f"✓ Fallback: Formatted slip written to diagnostics log: {diagnostics_path}")
        except Exception as diag_err:
            logger.error(f"Failed to write fallback diagnostics file: {str(diag_err)}")

    # 2. Write dry-run text log to pump_exports directory
    safe_party = "".join(c for c in party_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    receipt_filename = f"receipt_{safe_party}_{timestamp}.txt"
    receipt_path = os.path.join(get_receipts_dir(), receipt_filename)
    
    try:
        with open(receipt_path, "w", encoding="utf-8") as f:
            f.write(ascii_slip)
        logger.info(f"✓ Dry-run ASCII receipt layout cached successfully: {receipt_path}")
    except Exception as e:
        logger.error(f"Failed to cache dry-run receipt on disk: {str(e)}")

    # 3. Port Auto-Discovery Check
    printer_diagnostics = connect_thermal_printer()
    
    # Execute dry run if requested, or if no local hardware was discovered
    if dry_run or not printer_diagnostics["connected"]:
        msg = "Dry-run mode active. Slip layout saved to disk." if dry_run else f"No receipt printer discovered. Fallback dry-run saved. ({printer_diagnostics['message']})"
        logger.info(msg)
        write_fallback_diagnostics()
        return {
            "status": "success",
            "message": msg,
            "receipt_path": receipt_path,
            "ascii_preview": ascii_slip,
            "dry_run": True,
            "printer": printer_diagnostics["printer_name"]
        }

    # 4. Compile styled ESC/POS Hardware Command Byte Stream
    raw_stream = bytearray()
    
    # Initialize Printer
    raw_stream.extend(INIT_PRINTER)
    
    # Center Bold Brand Header
    raw_stream.extend(ALIGN_CENTER)
    raw_stream.extend(BOLD_ON)
    raw_stream.extend(b"PUMPAI FUEL STATION\n")
    raw_stream.extend(BOLD_OFF)
    raw_stream.extend(b"Trust & Statutory Integrity\n")
    
    # Formatted Time
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n"
    raw_stream.extend(time_str.encode("ascii"))
    raw_stream.extend(b"------------------------------------------------\n")
    
    # Left Aligned Customer Info
    raw_stream.extend(ALIGN_LEFT)
    raw_stream.extend(f"Customer Name: {party_name}\n".encode("ascii"))
    raw_stream.extend(b"------------------------------------------------\n")
    
    # Grid Header
    raw_stream.extend(b"Date        Vehicle No            Amount (INR)\n")
    raw_stream.extend(b"------------------------------------------------\n")
    
    # Rows iteration
    for tx in transactions:
        tx_date = tx.get("date", "")
        vehicle = tx.get("vehicle_no") or tx.get("vehicle_wheel_no") or "N/A"
        if len(vehicle) > 15:
            vehicle = vehicle[:12] + "..."
        amount = tx.get("amount", 0.0)
        amount_str = f"{amount:,.2f}"
        
        row_str = "{:<10}  {:<15}{:>21}\n".format(tx_date, vehicle, amount_str)
        raw_stream.extend(row_str.encode("ascii"))
        
    raw_stream.extend(b"------------------------------------------------\n")
    
    # Double-Sized Bold Total Outstanding Summary
    raw_stream.extend(ALIGN_CENTER)
    raw_stream.extend(FONT_DOUBLE)
    raw_stream.extend(BOLD_ON)
    total_str = f"TOTAL DUE: INR {net_due:,.2f}\n"
    raw_stream.extend(total_str.encode("ascii"))
    raw_stream.extend(BOLD_OFF)
    raw_stream.extend(FONT_NORMAL)
    
    # Footer and Cut
    raw_stream.extend(b"------------------------------------------------\n")
    raw_stream.extend(b"Thank you for your business!\n")
    raw_stream.extend(b"Please settle dues on time.\n")
    raw_stream.extend(b"------------------------------------------------\n")
    raw_stream.extend(b"\n\n\n\n")  # Feed lines
    raw_stream.extend(CUT_PAPER)

    # 5. Raw Spool via OS native CUPS/lp architecture
    printer_name = printer_diagnostics["printer_name"]
    logger.info(f"Spooling {len(raw_stream)} ESC/POS raw bytes to system printer: {printer_name}")
    
    try:
        # macOS/Linux standard spool command
        process = subprocess.Popen(
            ["lp", "-d", printer_name, "-o", "raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = process.communicate(input=raw_stream, timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(cmd=["lp", "-d", printer_name, "-o", "raw"], timeout=3, output=stdout, stderr=stderr)
        
        if process.returncode == 0:
            logger.info("✓ ESC/POS bytes successfully spooled to local print queue!")
            return {
                "status": "success",
                "message": f"Slip spooled successfully to printer '{printer_name}'.",
                "receipt_path": receipt_path,
                "ascii_preview": ascii_slip,
                "dry_run": False,
                "printer": printer_name
            }
        else:
            err_msg = stderr.decode("utf-8") if stderr else "Unknown spooler error"
            logger.warning(f"Printer spooling failed: {err_msg}. Falling back to dry-run layout.")
            write_fallback_diagnostics()
            return {
                "status": "success",
                "message": f"Hardware spooling failed: {err_msg}. Saved dry-run receipt to disk.",
                "receipt_path": receipt_path,
                "ascii_preview": ascii_slip,
                "dry_run": True,
                "printer": printer_name
            }
            
    except Exception as spool_err:
        logger.warning(f"Spool exception encountered: {str(spool_err)}. Falling back to dry run.")
        write_fallback_diagnostics()
        return {
            "status": "success",
            "message": f"Spool exception: {str(spool_err)}. Saved dry-run receipt to disk.",
            "receipt_path": receipt_path,
            "ascii_preview": ascii_slip,
            "dry_run": True,
            "printer": printer_name
        }
