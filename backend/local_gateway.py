#!/usr/bin/env python3
"""
Local Network Client Presentation Gateway.
Exposes a read-only endpoint served on the local network for viewing customer accounts.
Uses rotating daily salts and hashes to prevent endpoint scanning, and streams QR codes.
"""

import os
import sqlite3
import hashlib
import socket
import logging
import io
import json
from datetime import date
from typing import Optional
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse

# Setup logging
logger = logging.getLogger("LocalGateway")
logger.setLevel(logging.INFO)

router = APIRouter()

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def get_lan_ip() -> str:
    """Detects the local Wi-Fi/LAN IP address of the server."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_daily_salt() -> str:
    """Generates a temporary secret salt token that changes every calendar day."""
    secret = os.environ.get("GATEWAY_SECRET", "FuelSync_Secure_Gateway_Salt_2026_Secret")
    today_str = date.today().isoformat()
    return f"{secret}_{today_str}"

def get_party_hash(party_name: str) -> str:
    """Generates a non-predictable, unique hash for a party name using the daily salt."""
    salt = get_daily_salt()
    canonical_name = party_name.strip().lower()
    raw_str = f"{canonical_name}:{salt}"
    return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

def resolve_party_name_from_hash(party_hash: str) -> Optional[str]:
    """Matches a hash to a unique decrypted customer name from the ledger database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT party_name FROM ledger_entries")
        rows = cursor.fetchall()
        conn.close()
    except Exception as db_err:
        logger.error(f"Failed to query database during hash resolution: {str(db_err)}")
        return None

    from crypto_vault import decrypt_field

    for row in rows:
        party_enc = row[0]
        try:
            party_name = decrypt_field(party_enc, return_type=str)
        except Exception:
            party_name = str(party_enc or "")

        if party_name:
            if get_party_hash(party_name) == party_hash:
                return party_name

    return None

@router.get("/api/share/link")
def get_share_link(party_name: str):
    """Generates the local network presentation URL containing the secure hash."""
    if not party_name or not party_name.strip():
        raise HTTPException(status_code=400, detail="Party name cannot be empty")
        
    lan_ip = get_lan_ip()
    party_hash = get_party_hash(party_name)
    url = f"http://{lan_ip}:8000/share/ledger/{party_hash}"
    return {"status": "success", "hash": party_hash, "url": url}

@router.get("/api/share/qr")
def get_share_qr(party_name: str):
    """Generates and streams a PNG QR code for the customer's secure share URL."""
    if not party_name or not party_name.strip():
        raise HTTPException(status_code=400, detail="Party name cannot be empty")
        
    try:
        import qrcode
    except ImportError:
        raise HTTPException(status_code=500, detail="qrcode library not installed")
        
    lan_ip = get_lan_ip()
    party_hash = get_party_hash(party_name)
    url = f"http://{lan_ip}:8000/share/ledger/{party_hash}"
    
    # Generate QR Code image
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return StreamingResponse(img_byte_arr, media_type="image/png")

@router.get("/share/ledger/{party_name_hash}", response_class=HTMLResponse)
def get_ledger_presentation(party_name_hash: str):
    """
    Renders a standalone, read-only, mobile-responsive HTML statement view
    for the customer matching the security hash.
    """
    party_name = resolve_party_name_from_hash(party_name_hash)
    if not party_name:
        raise HTTPException(status_code=404, detail="Secure link has expired or is invalid")
        
    # Query all database transactions for this customer
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, party_name, vehicle_wheel_no, amount, type, remarks
            FROM ledger_entries
            ORDER BY date ASC, entry_id ASC
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as db_err:
        logger.error(f"Failed to query database for presentation: {str(db_err)}")
        raise HTTPException(status_code=500, detail="Database query error")
        
    from crypto_vault import decrypt_field
    
    target_clean = party_name.strip().lower()
    transactions = []
    total_owed = 0.0
    total_paid = 0.0
    
    for row in rows:
        r_date = row[0]
        r_party_enc = row[1]
        r_vehicle = row[2] or "—"
        r_amount_enc = row[3]
        r_type = row[4]
        r_remarks = row[5] or "—"
        
        try:
            r_party = decrypt_field(r_party_enc, return_type=str)
            r_amount = decrypt_field(r_amount_enc, return_type=float)
        except Exception:
            r_party = str(r_party_enc or "")
            r_amount = float(r_amount_enc or 0.0)
            
        if not r_party or r_party.strip().lower() != target_clean:
            continue
            
        if r_type == "udhaar":
            total_owed += r_amount
        else:
            total_paid += r_amount
            
        transactions.append({
            "date": r_date,
            "vehicle_no": r_vehicle,
            "amount": r_amount,
            "type": r_type,
            "remarks": r_remarks
        })
        
    if not transactions:
        raise HTTPException(status_code=404, detail="No statement records available for this account")
        
    # Calculate running balance and compile HTML rows
    balance = total_owed - total_paid
    running_bal = 0.0
    rows_html = []
    
    for tx in transactions:
        t_type = tx["type"]
        amt = tx["amount"]
        
        if t_type == "udhaar":
            running_bal += amt
            badge_class = "badge-debit"
            badge_text = "Credit Sale"
            owed_str = f"₹{amt:,.2f}"
            paid_str = "—"
        else:
            running_bal -= amt
            badge_class = "badge-credit"
            badge_text = "Payment"
            owed_str = "—"
            paid_str = f"₹{amt:,.2f}"
            
        rows_html.append(f"""
            <tr>
              <td>{tx["date"]}</td>
              <td><span class="badge {badge_class}">{badge_text}</span></td>
              <td style="font-family: monospace;">{tx["vehicle_no"]}</td>
              <td style="color: var(--text-muted);">{tx["remarks"]}</td>
              <td class="num" style="color: { 'var(--bad)' if t_type == 'udhaar' else 'var(--ok)' }; font-weight: 500;">
                { '+' if t_type == 'udhaar' else '−' }₹{amt:,.2f}
              </td>
              <td class="num" style="font-weight: 600;">₹{running_bal:,.2f}</td>
            </tr>
        """)
        
    table_rows = "\n".join(rows_html)
    balance_color = "var(--bad)" if balance > 0 else ("var(--ok)" if balance < 0 else "var(--text)")
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Statement of Account - {party_name}</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0F172A;
      --card: #1E293B;
      --border: #334155;
      --text: #F8FAFC;
      --text-muted: #94A3B8;
      --primary: #6366F1;
      --ok: #10B981;
      --bad: #EF4444;
      --font: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      padding: 16px;
      line-height: 1.5;
    }}
    .container {{
      max-width: 800px;
      margin: 0 auto;
    }}
    .header {{
      margin-bottom: 24px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .header h1 {{
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }}
    .subtitle {{
      color: var(--text-muted);
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .kpi-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .kpi-card {{
      background: var(--card);
      border: 1px solid var(--border);
      padding: 16px;
      border-radius: 12px;
    }}
    .kpi-title {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 4px;
    }}
    .kpi-val {{
      font-size: 20px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }}
    .table-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: rgba(255,255,255,0.03);
      padding: 14px 16px;
      color: var(--text-muted);
      font-weight: 600;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    .num {{
      text-align: right;
      font-family: 'JetBrains Mono', monospace;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }}
    .badge-debit {{
      background: rgba(239, 68, 68, 0.15);
      color: var(--bad);
    }}
    .badge-credit {{
      background: rgba(16, 185, 129, 0.15);
      color: var(--ok);
    }}
    .footer {{
      margin-top: 32px;
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="subtitle">Statement of Account (Live Gateway)</div>
      <h1>{party_name}</h1>
    </div>
    
    <div class="kpi-row">
      <div class="kpi-card">
        <div class="kpi-title">Total Credit Drawn</div>
        <div class="kpi-val" style="color: var(--bad);">₹{total_owed:,.2f}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Total Payments Cleared</div>
        <div class="kpi-val" style="color: var(--ok);">₹{total_paid:,.2f}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Outstanding Balance</div>
        <div class="kpi-val" style="color: {balance_color};">₹{balance:,.2f}</div>
      </div>
    </div>
    
    <div class="table-card">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Vehicle / Ref</th>
              <th>Remarks</th>
              <th class="num">Amount (₹)</th>
              <th class="num">Running Bal (₹)</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </div>
    </div>
    
    <div class="footer">
      <p>Secure link generated dynamically. Expired links are automatically rotated daily.</p>
      <p style="margin-top: 4px; opacity: 0.7;">Powered by FuelSync Presentation Gateway</p>
    </div>
  </div>
</body>
</html>
"""
    return html_content
