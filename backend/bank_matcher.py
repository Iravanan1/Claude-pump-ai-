"""
Bank Statement Cross-Referencing Engine.

Parses local bank statement PDFs using PyMuPDF, then reconciles extracted
credit rows against digital/UPI/Paytm drops logged in the pump diary ledger.

Settlement Statuses:
    SETTLED_IN_BANK     — diary entry matched a bank credit within ±48 hours window
    UNSETTLED_MISSING_CASH — diary entry has NO matching bank credit found
    PENDING             — not yet cross-referenced (initial state)
"""

import os
import re
import sqlite3
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BankMatcher")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

# Settlement status constants
STATUS_SETTLED = "SETTLED_IN_BANK"
STATUS_UNSETTLED = "UNSETTLED_MISSING_CASH"
STATUS_PENDING = "PENDING"

# Tolerance for fuzzy-amount matching (0.5% of the diary amount, min ₹2)
AMOUNT_TOLERANCE_RATE = 0.005
AMOUNT_TOLERANCE_MIN = 2.0

# 48-hour settlement window (credit must land within ±2 days of diary date)
SETTLEMENT_WINDOW_DAYS = 2

# ---------------------------------------------------------------------------
# Schema Initialisation
# ---------------------------------------------------------------------------

def init_bank_matcher_db(db_path: str = DB_PATH) -> None:
    """
    Creates two tables:
      bank_statement_credits — raw rows extracted from PDF statements
      digital_settlement_status — settlement status for each diary digital entry
    """
    logger.info(f"Initializing bank_matcher tables in {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Table 1: raw bank statement credits
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bank_statement_credits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_name TEXT,
        transaction_date TEXT,
        description TEXT,
        utr_string TEXT,
        credit_amount REAL DEFAULT 0.0,
        debit_amount  REAL DEFAULT 0.0,
        uploaded_at   TEXT DEFAULT (datetime('now'))
    )
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_bsc_date
        ON bank_statement_credits (transaction_date)
    """)

    # Table 2: settlement status per digital diary line
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS digital_settlement_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        diary_date    TEXT NOT NULL,
        source_label  TEXT,           -- e.g. 'Paytm Drop', 'UPI', 'card_tender'
        diary_amount  REAL DEFAULT 0.0,
        matched_bank_credit_id INTEGER,
        matched_bank_date TEXT,
        matched_bank_amount REAL,
        settlement_status TEXT DEFAULT 'PENDING',
        reconciled_at TEXT,
        UNIQUE (diary_date, source_label, diary_amount)
    )
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_dss_date
        ON digital_settlement_status (diary_date)
    """)

    conn.commit()
    conn.close()
    logger.info("bank_matcher tables initialised successfully.")


# ---------------------------------------------------------------------------
# Part 1 — PDF Statement Text Extraction
# ---------------------------------------------------------------------------

# Bank-specific column-detection patterns.
# Each tuple: (regex_pattern, named groups needed)
#
# Groups required by downstream code:
#   txn_date  — transaction date string (various formats)
#   desc      — narration / description / UPI ref
#   credit    — credit amount (may be missing → 0)
#   debit     — debit amount  (may be missing → 0)
#
_BANK_PATTERNS: Dict[str, re.Pattern] = {
    # Generic patterns that work across HDFC, SBI, Axis, ICICI statements
    # Matches: DD/MM/YYYY or DD-MM-YYYY date, then any text, then optional debit, credit
    "generic": re.compile(
        r"(?P<txn_date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"        # date
        r"(?P<desc>[^\d\n]{4,120})"                             # narration
        r"(?:(?P<debit>[\d,]+(?:\.\d{1,2})?)\s+)?"             # optional debit
        r"(?P<credit>[\d,]+(?:\.\d{1,2})?)?"                   # optional credit
        r"\s*(?P<balance>[\d,]+(?:\.\d{1,2})?)?",              # optional balance
        re.IGNORECASE
    ),
    # SBI statement format: date, ref/UTR, debit, credit, balance on same line
    "SBI": re.compile(
        r"(?P<txn_date>\d{2}/\d{2}/\d{4})\s+"
        r"(?P<utr>[A-Z0-9]+)?\s*"
        r"(?P<desc>[^0-9\n]{4,80}?)\s+"
        r"(?:(?P<debit>[\d,]+\.\d{2})\s+)?"
        r"(?P<credit>[\d,]+\.\d{2})",
        re.IGNORECASE
    ),
    # HDFC statement: date, narration, amount (Cr/Dr suffix)
    "HDFC": re.compile(
        r"(?P<txn_date>\d{2}/\d{2}/\d{2})\s+"
        r"(?P<desc>.{6,100}?)\s+"
        r"(?P<amount>[\d,]+\.\d{2})\s*"
        r"(?P<cr_dr>Cr|Dr)?",
        re.IGNORECASE
    ),
}

_UTR_RE = re.compile(
    r"\b(?:UTR|Ref|Txn|IMPS|NEFT|RTGS|UPI)[:\s#-]*([A-Z0-9]{8,22})\b",
    re.IGNORECASE
)

_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d{1,2})?")


def _clean_amount(text: str) -> float:
    """Strips commas, spaces from an Indian-formatted number string."""
    if not text:
        return 0.0
    cleaned = text.replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _normalise_date(date_str: str) -> Optional[str]:
    """
    Tries a battery of Indian bank date formats and returns ISO YYYY-MM-DD,
    or None if it cannot parse.
    """
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%b-%Y",
    ]
    raw = date_str.strip()
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_bank_statement_pdf(
    pdf_path: str,
    bank_name: str = "generic",
) -> List[Dict[str, Any]]:
    """
    Extracts transaction rows from a local bank statement PDF using PyMuPDF.

    Parameters
    ----------
    pdf_path  : absolute path to the PDF file
    bank_name : hint for the bank (e.g. 'SBI', 'HDFC', 'Axis', 'generic').
                Falls back to generic pattern if the hint is unknown.

    Returns
    -------
    List of dicts with keys:
        bank_name, transaction_date (ISO), description, utr_string,
        credit_amount, debit_amount
    """
    try:
        import fitz
    except ImportError:
        raise ImportError(
            "PyMuPDF is required. Install with: pip install pymupdf"
        )

    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"Bank statement PDF not found: {pdf_path}")

    logger.info(f"Parsing bank statement PDF: {pdf_path} (bank={bank_name})")

    # Pick pattern — fall back to generic
    pattern_key = bank_name.upper() if bank_name.upper() in _BANK_PATTERNS else "generic"
    pattern = _BANK_PATTERNS[pattern_key]

    transactions: List[Dict[str, Any]] = []
    doc = fitz.open(pdf_path)

    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if len(line) < 10:
                continue

            # --- HDFC two-suffix Cr/Dr pattern ---
            if pattern_key == "HDFC":
                m = pattern.search(line)
                if not m:
                    continue
                raw_date = _normalise_date(m.group("txn_date"))
                if not raw_date:
                    continue
                amount = _clean_amount(m.group("amount"))
                cr_dr = (m.group("cr_dr") or "").strip().upper()
                credit = amount if cr_dr == "CR" else 0.0
                debit  = amount if cr_dr == "DR" else 0.0

                utr_m = _UTR_RE.search(line)
                utr_str = utr_m.group(1) if utr_m else ""
                desc = m.group("desc").strip()

                transactions.append({
                    "bank_name": bank_name,
                    "transaction_date": raw_date,
                    "description": desc,
                    "utr_string": utr_str,
                    "credit_amount": credit,
                    "debit_amount": debit,
                })
                continue

            # --- SBI / Generic pattern ---
            m = pattern.search(line)
            if not m:
                continue

            raw_date = _normalise_date(m.group("txn_date"))
            if not raw_date:
                continue

            desc  = (m.group("desc") or "").strip()
            utr_m = _UTR_RE.search(line)
            utr_str = utr_m.group(1) if utr_m else (
                m.groupdict().get("utr") or ""
            )

            credit = _clean_amount(m.groupdict().get("credit") or "")
            debit  = _clean_amount(m.groupdict().get("debit")  or "")

            # Skip rows where both credit and debit are zero (header rows, etc.)
            if credit == 0.0 and debit == 0.0:
                continue

            transactions.append({
                "bank_name": bank_name,
                "transaction_date": raw_date,
                "description": desc,
                "utr_string": str(utr_str).strip(),
                "credit_amount": credit,
                "debit_amount": debit,
            })

    page_count = len(doc)
    doc.close()
    logger.info(
        f"PDF parsing complete. Extracted {len(transactions)} transaction rows "
        f"from {page_count} pages."
    )
    return transactions




# ---------------------------------------------------------------------------
# Part 2 — Bulk-upload parsed rows into SQLite
# ---------------------------------------------------------------------------

def save_bank_statement_credits(
    transactions: List[Dict[str, Any]],
    db_path: str = DB_PATH,
) -> int:
    """
    Persists parsed PDF rows into bank_statement_credits table.
    Returns the count of rows inserted.
    """
    if not transactions:
        logger.warning("save_bank_statement_credits called with empty transaction list.")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    inserted = 0
    omc_to_log = []

    for txn in transactions:
        try:
            cursor.execute("""
            INSERT INTO bank_statement_credits
                (bank_name, transaction_date, description, utr_string, credit_amount, debit_amount)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                txn.get("bank_name", "unknown"),
                txn.get("transaction_date", ""),
                txn.get("description", ""),
                txn.get("utr_string", ""),
                float(txn.get("credit_amount", 0.0)),
                float(txn.get("debit_amount", 0.0)),
            ))
            inserted += 1
            
            # Check for OMC transfers and collect them to log after closing connection
            debit_val = float(txn.get("debit_amount", 0.0))
            if debit_val > 0.0:
                desc_val = str(txn.get("description", "")).upper()
                is_omc = any(kw in desc_val for kw in ["IOCL", "HPCL", "BPCL", "CHALAN", "INDIAN OIL", "HINDUSTAN PETROLEUM", "BHARAT PETROLEUM"])
                if is_omc:
                    omc_to_log.append(txn)
        except Exception as e:
            logger.warning(f"Skipping row due to insert error: {e} — {txn}")

    conn.commit()
    conn.close()

    # Log OMC transactions safely now that the primary lock is released
    for txn in omc_to_log:
        try:
            debit_val = float(txn.get("debit_amount", 0.0))
            from omc_reconciler import log_omc_transaction
            utr_val = str(txn.get("utr_string") or "").strip()
            if not utr_val:
                utr_val = f"DEP_{txn.get('transaction_date')}_{debit_val}"
            
            log_omc_transaction(
                db_path=db_path,
                date_str=txn.get("transaction_date"),
                reference_no=utr_val,
                description="ADVANCE_DEPOSIT",
                debit=0.0,
                credit=debit_val
            )
        except Exception as omc_bank_err:
            logger.warning(f"Failed to sync bank debit to OMC ledger: {str(omc_bank_err)}")

    logger.info(f"Inserted {inserted} bank credit rows into SQLite.")
    return inserted


# ---------------------------------------------------------------------------
# Part 3 — Cross-Matching Reconciliation Engine
# ---------------------------------------------------------------------------

def _get_digital_diary_entries(
    target_date: str,
    db_path: str,
) -> List[Dict[str, Any]]:
    """
    Returns all digital/UPI/Paytm/card drops logged for a specific diary date.
    Sources:
      • daily_ledger: upi_tender, paytm_transfers, card_tender fields
      • ledger_entries: entries with type in ('bank_drop', 'digital', 'upi', 'paytm')
    """
    entries = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # --- Source A: daily_ledger tender columns ---
    try:
        cursor.execute("""
        SELECT upi_tender, paytm_transfers, card_tender
        FROM daily_ledger WHERE date = ?
        """, (target_date,))
        row = cursor.fetchone()
        if row:
            if row["upi_tender"] and float(row["upi_tender"]) > 0:
                entries.append({
                    "source_label": "UPI Tender",
                    "diary_amount": float(row["upi_tender"]),
                    "diary_date": target_date,
                })
            if row["paytm_transfers"] and float(row["paytm_transfers"]) > 0:
                entries.append({
                    "source_label": "Paytm Drop",
                    "diary_amount": float(row["paytm_transfers"]),
                    "diary_date": target_date,
                })
            if row["card_tender"] and float(row["card_tender"]) > 0:
                entries.append({
                    "source_label": "Card Tender",
                    "diary_amount": float(row["card_tender"]),
                    "diary_date": target_date,
                })
    except Exception as e:
        logger.warning(f"Could not read daily_ledger digital tenders: {e}")

    # --- Source B: ledger_entries typed rows ---
    DIGITAL_TYPES = ("bank_drop", "digital", "upi", "paytm", "card")
    try:
        placeholders = ",".join("?" * len(DIGITAL_TYPES))
        cursor.execute(f"""
        SELECT party_name, amount, type, remarks
        FROM ledger_entries
        WHERE date = ? AND LOWER(type) IN ({placeholders})
        """, (target_date, *DIGITAL_TYPES))
        for row in cursor.fetchall():
            try:
                # Try to decrypt if encrypted, otherwise use as-is
                try:
                    from crypto_vault import decrypt_field
                    amt = decrypt_field(row["amount"], return_type=float)
                    party = decrypt_field(row["party_name"], return_type=str)
                except Exception:
                    amt = float(row["amount"] or 0.0)
                    party = str(row["party_name"] or "")

                if amt and amt > 0:
                    entries.append({
                        "source_label": f"{row['type'].title()} — {party}",
                        "diary_amount": amt,
                        "diary_date": target_date,
                    })
            except Exception as row_err:
                logger.warning(f"Skipping ledger entry decode: {row_err}")
    except Exception as e:
        logger.warning(f"Could not read ledger_entries digital drops: {e}")

    conn.close()
    logger.info(
        f"Retrieved {len(entries)} digital diary entries for {target_date}."
    )
    return entries


def _find_bank_credit_match(
    diary_amount: float,
    diary_date_iso: str,
    db_path: str,
    window_days: int = SETTLEMENT_WINDOW_DAYS,
) -> Optional[Dict[str, Any]]:
    """
    Scans bank_statement_credits for a credit row that:
      1. Has credit_amount within AMOUNT_TOLERANCE of diary_amount.
      2. Falls within ±window_days of the diary date.

    Returns the closest match dict, or None.
    """
    tolerance = max(diary_amount * AMOUNT_TOLERANCE_RATE, AMOUNT_TOLERANCE_MIN)
    lo = diary_amount - tolerance
    hi = diary_amount + tolerance

    try:
        diary_dt = datetime.strptime(diary_date_iso, "%Y-%m-%d")
    except ValueError:
        return None

    date_lo = (diary_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
    date_hi = (diary_dt + timedelta(days=window_days)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, bank_name, transaction_date, description, utr_string,
           credit_amount, debit_amount
    FROM bank_statement_credits
    WHERE credit_amount BETWEEN ? AND ?
      AND transaction_date BETWEEN ? AND ?
    ORDER BY ABS(credit_amount - ?) ASC, transaction_date ASC
    LIMIT 1
    """, (lo, hi, date_lo, date_hi, diary_amount))

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def reconcile_diary_against_bank(
    target_date: str,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """
    Performs a strict cross-examination of digital diary drops vs bank credits
    for a given target date.

    Algorithm:
      1. Pull all digital/UPI/Paytm diary entries for target_date.
      2. For each diary entry, scan bank_statement_credits for a matching
         credit within a ±48-hour clear window and ±0.5% amount tolerance.
      3. Write settlement status to digital_settlement_status table.
      4. Return a summary report dict.

    Returns
    -------
    {
      "target_date": str,
      "diary_entries_checked": int,
      "settled_count": int,
      "unsettled_count": int,
      "total_diary_digital": float,
      "total_settled_amount": float,
      "total_unsettled_amount": float,
      "unsettled_entries": [ {...} ],   ← the missing-cash entries
      "settled_entries": [ {...} ],
    }
    """
    logger.info(f"Starting bank reconciliation for diary date: {target_date}")

    diary_entries = _get_digital_diary_entries(target_date, db_path)

    settled = []
    unsettled = []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for entry in diary_entries:
        diary_amount   = entry["diary_amount"]
        source_label   = entry["source_label"]
        diary_date_iso = entry["diary_date"]

        match = _find_bank_credit_match(
            diary_amount=diary_amount,
            diary_date_iso=diary_date_iso,
            db_path=db_path,
        )

        if match:
            status = STATUS_SETTLED
            matched_id     = match["id"]
            matched_date   = match["transaction_date"]
            matched_amount = match["credit_amount"]
        else:
            status = STATUS_UNSETTLED
            matched_id     = None
            matched_date   = None
            matched_amount = None

        # Upsert into digital_settlement_status
        try:
            cursor.execute("""
            INSERT INTO digital_settlement_status
                (diary_date, source_label, diary_amount,
                 matched_bank_credit_id, matched_bank_date, matched_bank_amount,
                 settlement_status, reconciled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT (diary_date, source_label, diary_amount)
            DO UPDATE SET
                matched_bank_credit_id = excluded.matched_bank_credit_id,
                matched_bank_date      = excluded.matched_bank_date,
                matched_bank_amount    = excluded.matched_bank_amount,
                settlement_status      = excluded.settlement_status,
                reconciled_at          = excluded.reconciled_at
            """, (
                diary_date_iso,
                source_label,
                diary_amount,
                matched_id,
                matched_date,
                matched_amount,
                status,
            ))
        except Exception as e:
            logger.warning(f"digital_settlement_status upsert failed: {e}")

        record = {
            **entry,
            "settlement_status": status,
            "matched_bank_credit_id": matched_id,
            "matched_bank_date": matched_date,
            "matched_bank_amount": matched_amount,
        }

        if status == STATUS_SETTLED:
            settled.append(record)
        else:
            unsettled.append(record)

    conn.commit()
    conn.close()

    total_diary   = sum(e["diary_amount"] for e in diary_entries)
    total_settled = sum(e["diary_amount"] for e in settled)
    total_missing = sum(e["diary_amount"] for e in unsettled)

    result = {
        "target_date": target_date,
        "diary_entries_checked": len(diary_entries),
        "settled_count": len(settled),
        "unsettled_count": len(unsettled),
        "total_diary_digital": round(total_diary, 2),
        "total_settled_amount": round(total_settled, 2),
        "total_unsettled_amount": round(total_missing, 2),
        "unsettled_entries": unsettled,
        "settled_entries": settled,
    }

    if unsettled:
        logger.warning(
            f"⚠️  DISCREPANCY FOUND: {len(unsettled)} diary digital entries "
            f"totalling ₹{total_missing:.2f} not found in bank credits for {target_date}."
        )
    else:
        logger.info(
            f"✓ All {len(settled)} diary digital entries reconciled successfully "
            f"with bank credits for {target_date}."
        )

    return result


# ---------------------------------------------------------------------------
# Part 4 — Query helpers for the frontend
# ---------------------------------------------------------------------------

def get_unsettled_digital_entries(
    db_path: str = DB_PATH,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Returns all UNSETTLED_MISSING_CASH entries across all dates,
    sorted by diary_date descending. Used by the frontend discrepancy panel.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
    SELECT diary_date, source_label, diary_amount,
           settlement_status, reconciled_at
    FROM digital_settlement_status
    WHERE settlement_status = ?
    ORDER BY diary_date DESC, diary_amount DESC
    LIMIT ?
    """, (STATUS_UNSETTLED, limit))

    rows = cursor.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_settlement_summary(
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """
    Returns aggregate totals across all dates for the frontend KPI strip.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN settlement_status = ? THEN 1 ELSE 0 END) as settled,
        SUM(CASE WHEN settlement_status = ? THEN 1 ELSE 0 END) as unsettled,
        SUM(diary_amount) as total_amount,
        SUM(CASE WHEN settlement_status = ? THEN diary_amount ELSE 0 END) as settled_amount,
        SUM(CASE WHEN settlement_status = ? THEN diary_amount ELSE 0 END) as unsettled_amount
    FROM digital_settlement_status
    """, (STATUS_SETTLED, STATUS_UNSETTLED, STATUS_SETTLED, STATUS_UNSETTLED))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            "total": row["total"] or 0,
            "settled": row["settled"] or 0,
            "unsettled": row["unsettled"] or 0,
            "total_amount": round(float(row["total_amount"] or 0), 2),
            "settled_amount": round(float(row["settled_amount"] or 0), 2),
            "unsettled_amount": round(float(row["unsettled_amount"] or 0), 2),
        }
    return {
        "total": 0, "settled": 0, "unsettled": 0,
        "total_amount": 0.0, "settled_amount": 0.0, "unsettled_amount": 0.0,
    }


def calculate_matched_and_missing_in_memory(
    credits: List[Dict[str, Any]],
    db_path: str = DB_PATH
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Computes matched transaction hashes and missing digital drops in-memory
    without modifying the database, enabling an instant UI response.
    """
    import hashlib
    # Extract unique dates from the statement credits
    unique_dates = sorted(list(set(c["transaction_date"] for c in credits if c.get("transaction_date"))))
    
    diary_entries = []
    if unique_dates:
        try:
            start_dt = datetime.strptime(unique_dates[0], "%Y-%m-%d") - timedelta(days=SETTLEMENT_WINDOW_DAYS)
            end_dt = datetime.strptime(unique_dates[-1], "%Y-%m-%d") + timedelta(days=SETTLEMENT_WINDOW_DAYS)
            
            curr = start_dt
            while curr <= end_dt:
                date_str = curr.strftime("%Y-%m-%d")
                diary_entries.extend(_get_digital_diary_entries(date_str, db_path))
                curr += timedelta(days=1)
        except Exception as e:
            logger.warning(f"Error resolving date window in-memory match: {e}")
            
    matched_hashes = []
    missing_drops = []
    matched_credit_ids = set()
    
    for diary in diary_entries:
        diary_amount = diary["diary_amount"]
        diary_date_str = diary["diary_date"]
        
        try:
            diary_dt = datetime.strptime(diary_date_str, "%Y-%m-%d")
        except ValueError:
            continue
            
        tolerance = max(diary_amount * AMOUNT_TOLERANCE_RATE, AMOUNT_TOLERANCE_MIN)
        
        best_match = None
        best_diff = float("inf")
        best_idx = -1
        
        for idx, credit in enumerate(credits):
            if idx in matched_credit_ids:
                continue
                
            credit_amount = credit["credit_amount"]
            credit_date_str = credit["transaction_date"]
            if not credit_date_str:
                continue
                
            try:
                credit_dt = datetime.strptime(credit_date_str, "%Y-%m-%d")
            except ValueError:
                continue
                
            # Date window check
            if abs((credit_dt - diary_dt).days) > SETTLEMENT_WINDOW_DAYS:
                continue
                
            # Amount check
            if not (diary_amount - tolerance <= credit_amount <= diary_amount + tolerance):
                continue
                
            diff = abs(credit_amount - diary_amount)
            if diff < best_diff:
                best_diff = diff
                best_match = credit
                best_idx = idx
                
        if best_match:
            matched_credit_ids.add(best_idx)
            # Generate a consistent transaction match hash
            match_key = f"{diary_date_str}_{diary['source_label']}_{diary_amount}_{best_match['transaction_date']}_{best_match['utr_string']}"
            match_hash = hashlib.sha256(match_key.encode("utf-8")).hexdigest()
            matched_hashes.append(match_hash)
        else:
            missing_drops.append({
                "diary_date": diary_date_str,
                "source_label": diary["source_label"],
                "diary_amount": diary_amount,
                "settlement_status": STATUS_UNSETTLED
            })
            
    return matched_hashes, missing_drops
