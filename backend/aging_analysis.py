#!/usr/bin/env python3
"""
Credit Ledger Aging Analysis & Risk Evaluation Engine.
Tracks aging categories of customer outstanding debts, computes risk coefficients,
and compiles statutory PDF risk statement reports.
"""

import os
import sqlite3
import logging
import datetime
from datetime import datetime as dt
from typing import List, Dict, Any, Optional
import pandas as pd

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

logger = logging.getLogger("AgingAnalysis")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def compile_customer_debt_aging(
    party_name: str,
    reference_date: Optional[str] = None,
    db_path: str = DB_PATH
) -> dict:
    """
    Groups all outstanding UNPAID or PARTIALLY_PAID credit entries for a customer
    into four chronological time buckets relative to the reference date:
      - Current Debt (0 to 15 Days Old)
      - Growing Debt (16 to 30 Days Old)
      - Delinquent Debt (31 to 60 Days Old)
      - High-Risk Critical Debt (More than 60 Days Old)
    
    Computes a risk profile percentage coefficient and returns alert flags.
    """
    from fifo_settler import _get_unpaid_udhaar_rows, ensure_fifo_columns
    
    ensure_fifo_columns(db_path)
    unpaid_rows = _get_unpaid_udhaar_rows(party_name, db_path)
    
    # Resolve reference date
    if reference_date:
        try:
            ref_dt = dt.strptime(reference_date.strip(), "%Y-%m-%d").date()
        except Exception:
            ref_dt = datetime.date.today()
    else:
        ref_dt = datetime.date.today()
        
    current_debt = 0.0
    growing_debt = 0.0
    delinquent_debt = 0.0
    critical_debt = 0.0
    
    for row in unpaid_rows:
        date_str = row["date"]
        try:
            entry_dt = dt.strptime(date_str.strip(), "%Y-%m-%d").date()
        except Exception:
            continue
            
        days_old = (ref_dt - entry_dt).days
        amt = float(row["effective_outstanding"])
        
        if days_old <= 15:
            current_debt += amt
        elif days_old <= 30:
            growing_debt += amt
        elif days_old <= 60:
            delinquent_debt += amt
        else:
            critical_debt += amt
            
    total_outstanding = current_debt + growing_debt + delinquent_debt + critical_debt
    
    # Calculate Risk Coefficient percentage
    if total_outstanding > 0:
        weighted_risk = (0.0 * current_debt) + (0.15 * growing_debt) + (0.50 * delinquent_debt) + (1.00 * critical_debt)
        risk_pct = round((weighted_risk / total_outstanding) * 100, 2)
    else:
        risk_pct = 0.0
        
    collection_required = (critical_debt > 0.0)
    
    return {
        "party_name": party_name,
        "reference_date": ref_dt.strftime("%Y-%m-%d"),
        "total_outstanding": round(total_outstanding, 2),
        "buckets": {
            "current": round(current_debt, 2),
            "growing": round(growing_debt, 2),
            "delinquent": round(delinquent_debt, 2),
            "critical": round(critical_debt, 2)
        },
        "risk_coefficient_pct": risk_pct,
        "collection_required_alert": collection_required
    }

def compile_all_customers_aging(
    reference_date: Optional[str] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Compiles chronological debt aging buckets and risk profiles for all unique credit customers.
    """
    from crypto_vault import decrypt_field
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT party_name FROM ledger_entries WHERE type = 'udhaar'")
    rows = cursor.fetchall()
    conn.close()
    
    unique_parties = set()
    for r in rows:
        party_enc = r[0]
        try:
            dec = decrypt_field(party_enc, return_type=str)
            if dec:
                unique_parties.add(dec.strip())
        except Exception:
            unique_parties.add(str(party_enc).strip())
            
    summary_list = []
    for party in sorted(unique_parties):
        stats = compile_customer_debt_aging(party, reference_date, db_path)
        if stats["total_outstanding"] > 0:
            summary_list.append(stats)
            
    # Sort by total outstanding descending
    summary_list.sort(key=lambda x: x["total_outstanding"], reverse=True)
    return summary_list

def export_aging_summary_pdf(
    output_path: Optional[str] = None,
    reference_date: Optional[str] = None,
    db_path: str = DB_PATH
) -> str:
    """
    Compiles customer credit ledger balances, formats them using Pandas,
    and renders a beautiful multi-page PDF credit risk statement using PyMuPDF.
    """
    if fitz is None:
        raise ImportError("PyMuPDF is required to export aging statement PDFs. Install it with: pip install pymupdf")
        
    # Resolve folders
    if not output_path:
        WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)
        EXPORTS_DIR = os.path.join(WORKSPACE_DIR, "pump_exports")
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        output_path = os.path.join(EXPORTS_DIR, "credit_aging_summary.pdf")
        
    # 1. Compile aging data
    aging_data = compile_all_customers_aging(reference_date, db_path)
    
    # 2. Package into Pandas DataFrame
    records = []
    for customer in aging_data:
        records.append({
            "Customer": customer["party_name"],
            "Current (0-15d)": customer["buckets"]["current"],
            "Growing (16-30d)": customer["buckets"]["growing"],
            "Delinquent (31-60d)": customer["buckets"]["delinquent"],
            "Critical (>60d)": customer["buckets"]["critical"],
            "Total Outstanding": customer["total_outstanding"],
            "Risk Score": customer["risk_coefficient_pct"],
            "Alert": "COLLECTION REQUIRED" if customer["collection_required_alert"] else "HEALTHY"
        })
        
    df = pd.DataFrame(records) if records else pd.DataFrame(columns=[
        "Customer", "Current (0-15d)", "Growing (16-30d)", "Delinquent (31-60d)", "Critical (>60d)", "Total Outstanding", "Risk Score", "Alert"
    ])
    
    # Calculate global totals
    total_current = df["Current (0-15d)"].sum() if not df.empty else 0.0
    total_growing = df["Growing (16-30d)"].sum() if not df.empty else 0.0
    total_delinquent = df["Delinquent (31-60d)"].sum() if not df.empty else 0.0
    total_critical = df["Critical (>60d)"].sum() if not df.empty else 0.0
    total_outstanding = df["Total Outstanding"].sum() if not df.empty else 0.0
    
    # 3. Render A4 Landscape PDF using fitz
    PAGE_W, PAGE_H = 842, 595  # Landscape
    MARGIN_L, MARGIN_R = 40, 40
    MARGIN_T, MARGIN_B = 50, 45
    TITLE_H = 46
    HEADER_H = 26
    ROW_H = 22
    
    # Colors
    C_HEADER_BG = (0.12, 0.29, 0.49)  # 1F497D Navy
    C_WHITE = (1.0, 1.0, 1.0)
    C_GRAY_LIGHT = (0.97, 0.97, 0.98)
    C_GRAY_BORDER = (0.86, 0.86, 0.86)
    C_RED_ALERT = (0.85, 0.18, 0.18)
    C_GREEN_OK = (0.18, 0.6, 0.18)
    C_TEXT = (0.15, 0.15, 0.15)
    C_SUBTEXT = (0.4, 0.4, 0.4)
    
    # Columns: Customer (24%), Current (11%), Growing (11%), Delinquent (11%), Critical (11%), Total (11%), Risk (10%), Alert (11%)
    COL_PROPS = [0.24, 0.11, 0.11, 0.11, 0.11, 0.11, 0.10, 0.11]
    COL_HEADERS = [
        "Customer / Party Name", "Current (0-15d)", "Growing (16-30d)", "Delinquent (31-60d)",
        "Critical (>60d)", "Total Owed", "Risk Coefficient", "Status Alert"
    ]
    
    # Calculate column X coordinates
    TABLE_W = PAGE_W - MARGIN_L - MARGIN_R
    col_widths = [p * TABLE_W for p in COL_PROPS]
    col_x = []
    curr_x = MARGIN_L
    for w in col_widths:
        col_x.append(curr_x)
        curr_x += w
        
    ROWS_PER_PAGE = int((PAGE_H - MARGIN_T - MARGIN_B - TITLE_H - HEADER_H) // ROW_H) - 2 # Leave space for totals
    total_pages = max(1, -(-len(df) // ROWS_PER_PAGE))
    
    doc = fitz.open()
    
    for page_num in range(total_pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        
        # Draw Title Banner
        title_rect = fitz.Rect(MARGIN_L, MARGIN_T, PAGE_W - MARGIN_R, MARGIN_T + TITLE_H)
        page.draw_rect(title_rect, color=None, fill=C_HEADER_BG, width=0)
        
        title_text = "CREDIT LEDGER AGING & DEBT RISK ANALYSIS"
        ref_str = reference_date if reference_date else dt.today().strftime("%Y-%m-%d")
        subtitle_text = f"Outstanding Credit Risk Statement — Reference Date: {ref_str}"
        
        page.insert_text(
            (MARGIN_L + 15, MARGIN_T + 18),
            title_text, fontname="Helvetica-Bold", fontsize=12, color=C_WHITE
        )
        page.insert_text(
            (MARGIN_L + 15, MARGIN_T + 34),
            subtitle_text, fontname="Helvetica", fontsize=8, color=(0.8, 0.88, 1.0)
        )
        
        # Generation stamp
        ts = dt.now().strftime("Generated: %d-%b-%Y %H:%M")
        page.insert_text(
            (MARGIN_L, PAGE_H - MARGIN_B + 16),
            ts, fontname="Helvetica", fontsize=7.5, color=C_SUBTEXT
        )
        
        # Page count
        pg_text = f"Page {page_num + 1} of {total_pages}"
        pgw = fitz.get_text_length(pg_text, fontname="Helvetica", fontsize=7.5)
        page.insert_text(
            (PAGE_W - MARGIN_R - pgw, PAGE_H - MARGIN_B + 16),
            pg_text, fontname="Helvetica", fontsize=7.5, color=C_SUBTEXT
        )
        
        # Draw Table Headers
        hy = MARGIN_T + TITLE_H + 10
        page.draw_rect(fitz.Rect(MARGIN_L, hy, PAGE_W - MARGIN_R, hy + HEADER_H), color=None, fill=C_HEADER_BG, width=0)
        
        for idx, header in enumerate(COL_HEADERS):
            align = 0 if idx == 0 else (2 if idx == 7 else 1) # Left align customer, center/right align numbers/status
            hx = col_x[idx]
            hw = col_widths[idx]
            text_y = hy + 17
            
            if align == 0:
                tx = hx + 6
            elif align == 2:
                tw = fitz.get_text_length(header, fontname="Helvetica-Bold", fontsize=8)
                tx = hx + (hw - tw) / 2
            else:
                tw = fitz.get_text_length(header, fontname="Helvetica-Bold", fontsize=8)
                tx = hx + (hw - tw) / 2
                
            page.insert_text((tx, text_y), header, fontname="Helvetica-Bold", fontsize=8, color=C_WHITE)
            
        # Draw Data Rows
        dy = hy + HEADER_H
        page_records = df.iloc[page_num * ROWS_PER_PAGE:(page_num + 1) * ROWS_PER_PAGE]
        
        for ridx, row in enumerate(page_records.itertuples()):
            ry = dy + ridx * ROW_H
            
            # Alternating row backgrounds
            if ridx % 2 == 1:
                page.draw_rect(fitz.Rect(MARGIN_L, ry, PAGE_W - MARGIN_R, ry + ROW_H), color=None, fill=C_GRAY_LIGHT, width=0)
                
            # Draw cell borders
            page.draw_rect(fitz.Rect(MARGIN_L, ry, PAGE_W - MARGIN_R, ry + ROW_H), color=C_GRAY_BORDER, fill=None, width=0.5)
            
            # Print Customer (Col 0)
            page.insert_text((col_x[0] + 6, ry + 14), str(row[1]), fontname="Helvetica-Bold", fontsize=8.5, color=C_TEXT)
            
            # Print numbers
            for col_i in range(1, 6):
                val = row[col_i + 1]
                val_str = f"{val:,.2f}" if val > 0 else "—"
                cx = col_x[col_i]
                cw = col_widths[col_i]
                vw = fitz.get_text_length(val_str, fontname="Helvetica", fontsize=8.5)
                # Right aligned
                page.insert_text((cx + cw - vw - 6, ry + 14), val_str, fontname="Helvetica", fontsize=8.5, color=C_TEXT)
                
            # Print Risk Coefficient (Col 6)
            risk_val = row[7]
            risk_str = f"{risk_val:.1f}%"
            cx = col_x[6]
            cw = col_widths[6]
            rw = fitz.get_text_length(risk_str, fontname="Helvetica-Bold", fontsize=8.5)
            # Centered
            page.insert_text((cx + (cw - rw) / 2, ry + 14), risk_str, fontname="Helvetica-Bold", fontsize=8.5, color=C_TEXT)
            
            # Print Status Alert (Col 7)
            alert_str = str(row[8])
            cx = col_x[7]
            cw = col_widths[7]
            aw = fitz.get_text_length(alert_str, fontname="Helvetica-Bold", fontsize=7.5)
            # Draw badge background
            bx = cx + (cw - aw) / 2 - 4
            by = ry + 4
            bw = aw + 8
            bh = 13
            b_color = C_RED_ALERT if alert_str == "COLLECTION REQUIRED" else C_GREEN_OK
            page.draw_rect(fitz.Rect(bx, by, bx + bw, by + bh), color=None, fill=b_color, width=0)
            page.insert_text((bx + 4, ry + 13), alert_str, fontname="Helvetica-Bold", fontsize=7.5, color=C_WHITE)
            
        # Draw Totals on the last page
        if page_num == total_pages - 1:
            total_y = dy + len(page_records) * ROW_H
            # Banner background
            page.draw_rect(fitz.Rect(MARGIN_L, total_y, PAGE_W - MARGIN_R, total_y + ROW_H), color=None, fill=C_HEADER_BG, width=0)
            
            # Label
            page.insert_text((col_x[0] + 6, total_y + 14), "GRAND CREDIT TOTALS", fontname="Helvetica-Bold", fontsize=8.5, color=C_WHITE)
            
            # Sum columns
            sums = [total_current, total_growing, total_delinquent, total_critical, total_outstanding]
            for col_i in range(1, 6):
                val = sums[col_i - 1]
                val_str = f"{val:,.2f}"
                cx = col_x[col_i]
                cw = col_widths[col_i]
                vw = fitz.get_text_length(val_str, fontname="Helvetica-Bold", fontsize=8.5)
                page.insert_text((cx + cw - vw - 6, total_y + 14), val_str, fontname="Helvetica-Bold", fontsize=8.5, color=C_WHITE)
                
            # Average Risk Coefficient
            global_risk = 0.0
            if total_outstanding > 0:
                weighted_total = (0.15 * total_growing) + (0.50 * total_delinquent) + (1.00 * total_critical)
                global_risk = round((weighted_total / total_outstanding) * 100, 2)
            global_risk_str = f"{global_risk:.1f}%"
            cx = col_x[6]
            cw = col_widths[6]
            rw = fitz.get_text_length(global_risk_str, fontname="Helvetica-Bold", fontsize=8.5)
            page.insert_text((cx + (cw - rw) / 2, total_y + 14), global_risk_str, fontname="Helvetica-Bold", fontsize=8.5, color=C_WHITE)
            
    doc.save(output_path)
    doc.close()
    
    logger.info(f"✓ Credit Aging risk statement PDF generated and styled successfully at: {output_path}")
    return output_path
