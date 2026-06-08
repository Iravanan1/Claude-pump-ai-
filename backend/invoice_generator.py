"""
Automated Credit Account Invoicing & Statement Compiler.

Aggregates historical ledger transactions and generates minimalist business
statement invoice PDFs using ReportLab.
"""

import os
import sqlite3
import logging
from typing import Dict, Any, List

# Decryptor helper
try:
    from crypto_vault import decrypt_field
except ImportError:
    def decrypt_field(val, return_type=str):
        if val is None:
            return None
        return return_type(val)

logger = logging.getLogger("InvoiceGenerator")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")
EXPORTS_DIR = os.path.join(WORKSPACE_DIR, "pump_exports", "customer_invoices")


def generate_customer_invoice(
    party_name: str,
    start_date: str,
    end_date: str,
    db_path: str = DB_PATH,
    exports_dir: str = EXPORTS_DIR
) -> str:
    """
    Aggregates ledger entries for a target customer within a date window
    and generates a minimalist business invoice PDF.
    
    Parameters
    ----------
    party_name : str - name of the customer account
    start_date : str - 'YYYY-MM-DD' start date
    end_date   : str - 'YYYY-MM-DD' end date
    db_path    : str - path to sqlite ledger database
    exports_dir: str - folder destination for PDF
    
    Returns
    -------
    str - path to the generated invoice PDF
    """
    logger.info(f"Generating statement invoice for '{party_name}' from {start_date} to {end_date}...")
    os.makedirs(exports_dir, exist_ok=True)
    
    # 1. Fetch & Decrypt ledger entries
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, party_name, vehicle_wheel_no, amount, type, remarks 
        FROM ledger_entries
        ORDER BY date ASC, entry_id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    target_clean = party_name.strip().lower()
    
    opening_balance = 0.0
    itemized_lines = []
    
    accumulated_debits = 0.0
    payments_received = 0.0
    
    # Process rows
    for row in rows:
        r_date = row[0]
        r_party_enc = row[1]
        r_vehicle = row[2] or "N/A"
        r_amount_enc = row[3]
        r_type = row[4]
        r_remarks = row[5] or ""
        
        # Decrypt fields
        try:
            r_party = decrypt_field(r_party_enc, return_type=str)
            r_amount = decrypt_field(r_amount_enc, return_type=float)
        except Exception:
            r_party = str(r_party_enc or "")
            r_amount = float(r_amount_enc or 0.0)
            
        if not r_party or r_party.strip().lower() != target_clean:
            continue
            
        # Is it before the billing period?
        if r_date < start_date:
            if r_type == "udhaar":
                opening_balance += r_amount
            elif r_type in ("payment", "deposit", "receipt"):
                opening_balance -= r_amount
            elif r_amount < 0:
                opening_balance += r_amount
        # Is it within the billing period?
        elif start_date <= r_date <= end_date:
            is_debit = False
            if r_type == "udhaar":
                is_debit = True
                accumulated_debits += r_amount
            elif r_type in ("payment", "deposit", "receipt"):
                is_debit = False
                payments_received += r_amount
            elif r_amount < 0:
                is_debit = True
                accumulated_debits += r_amount
            else:
                is_debit = True
                accumulated_debits += r_amount
                
            itemized_lines.append({
                "date": r_date,
                "vehicle": r_vehicle,
                "amount": r_amount,
                "type": r_type,
                "remarks": r_remarks,
                "is_debit": is_debit
            })
            
    final_due = opening_balance + accumulated_debits - payments_received
    
    # 2. PDF Formatting using ReportLab
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    
    pdf_filename = f"Invoice_{party_name.replace(' ', '_')}_{end_date}.pdf"
    pdf_path = os.path.join(exports_dir, pdf_filename)
    
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        rightMargin=54,
        leftMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    
    # Custom elegant styles
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#1F497D'),
        spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'InvoiceSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#555555'),
        spaceAfter=15
    )
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#1F497D'),
        spaceAfter=8,
        spaceBefore=15
    )
    cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#222222')
    )
    cell_header = ParagraphStyle(
        'TableHeader',
        parent=cell_style,
        fontName='Helvetica-Bold',
        textColor=colors.white
    )
    summary_label = ParagraphStyle(
        'SummaryLabel',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#444444')
    )
    summary_value = ParagraphStyle(
        'SummaryValue',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        alignment=2,  # Right-aligned
        textColor=colors.HexColor('#222222')
    )
    summary_total_value = ParagraphStyle(
        'SummaryTotalValue',
        parent=summary_value,
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#1F497D')
    )
    
    story = []
    
    # Pump Header Info
    story.append(Paragraph("PumpAI Fuel Station", title_style))
    story.append(Paragraph(
        f"<b>Statement Billing Period:</b> {start_date} to {end_date}<br/>"
        f"<b>Account Customer:</b> {party_name}", subtitle_style
    ))
    story.append(Spacer(1, 10))
    
    # Itemized Table
    story.append(Paragraph("Itemized Transaction Statement", section_heading))
    
    # Table headers
    table_data = [
        [
            Paragraph("Date", cell_header),
            Paragraph("Vehicle / Details", cell_header),
            Paragraph("Type", cell_header),
            Paragraph("Remarks", cell_header),
            Paragraph("Amount (INR)", cell_header)
        ]
    ]
    
    # Table rows
    for line in itemized_lines:
        sign = "" if line["is_debit"] else "-"
        amt_str = f"₹{sign}{line['amount']:,.2f}"
        type_str = "Debit (Udhaar)" if line["is_debit"] else "Payment"
        
        table_data.append([
            Paragraph(line["date"], cell_style),
            Paragraph(line["vehicle"], cell_style),
            Paragraph(type_str, cell_style),
            Paragraph(line["remarks"], cell_style),
            Paragraph(amt_str, cell_style)
        ])
        
    if len(itemized_lines) == 0:
        table_data.append([
            Paragraph("No transactions recorded during this period.", cell_style),
            "", "", "", ""
        ])
        
    # Standard table widths (total 504 points for Letter page with 54 margins)
    col_widths = [75, 100, 80, 159, 90]
    
    t = Table(table_data, colWidths=col_widths)
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F497D')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D9D9D9')),
    ]
    
    # Alternating row colors
    for i in range(1, len(table_data)):
        bg = colors.HexColor('#F9FBFD') if i % 2 == 1 else colors.white
        t_style.append(('BACKGROUND', (0, i), (-1, i), bg))
        t_style.append(('BOTTOMPADDING', (0, i), (-1, i), 6))
        t_style.append(('TOPPADDING', (0, i), (-1, i), 6))
        
    if len(itemized_lines) == 0:
        t_style.append(('SPAN', (0, 1), (-1, 1)))
        
    t.setStyle(TableStyle(t_style))
    story.append(t)
    story.append(Spacer(1, 20))
    
    # Summary Block
    summary_data = [
        [Paragraph("Opening Balance:", summary_label), Paragraph(f"₹{opening_balance:,.2f}", summary_value)],
        [Paragraph("New Accumulated Debits (Udhaar):", summary_label), Paragraph(f"₹{accumulated_debits:,.2f}", summary_value)],
        [Paragraph("Payments Received:", summary_label), Paragraph(f"₹{payments_received:,.2f}", summary_value)],
        [Paragraph("<b>Final Net Outstanding Due:</b>", summary_label), Paragraph(f"<b>₹{final_due:,.2f}</b>", summary_total_value)]
    ]
    
    # Summary Table Widths: 350 for labels, 154 for values
    st = Table(summary_data, colWidths=[350, 154])
    st.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor('#EAEAEA')),
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.HexColor('#1F497D')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    
    story.append(KeepTogether([
        Paragraph("Account Statement Summary", section_heading),
        st
    ]))
    
    # Build Document
    doc.build(story)
    logger.info(f"Customer statement invoice generated successfully at: {pdf_path}")
    return pdf_path
