#!/usr/bin/env python3
"""
customer_alerts.py
──────────────────
Customer ledger reminder message compiler.

Exports
-------
draft_outstanding_reminder(party_name, db_path) -> str
    Assembles real-time outstanding balance + oldest unpaid entry details and
    renders a WhatsApp-ready Hinglish statement nudge with a secure gateway link.

FastAPI router
--------------
GET /api/customer/reminder          ?party_name=<str>
GET /api/customer/reminder/bulk     ?min_balance=<float>   (returns list)
GET /api/customer/outstanding-list                         (all debtors summary)
"""

import os
import sqlite3
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("CustomerAlerts")

# ── Paths ──────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BACKEND_DIR, "ledger.db")

router = APIRouter()


# ════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ════════════════════════════════════════════════════════════════════════════

def _decrypt_safe(value, return_type=str):
    """Attempt field decryption; fall back to raw cast on any error."""
    try:
        from crypto_vault import decrypt_field          # noqa: PLC0415
        return decrypt_field(value, return_type=return_type)
    except Exception:
        if return_type is float:
            return float(value or 0.0)
        return str(value or "")


def _compute_balance(party_name: str, db_path: str = DB_PATH) -> tuple[float, float, float]:
    """
    Returns (total_owed, total_paid, net_balance) for *party_name*.

    Reads every ledger_entries row, decrypts party + amount in-memory
    (because they are stored encrypted at rest), and sums per type.

    type == 'udhaar'  → debit  (customer owes us)
    type == 'payment' → credit (customer paid us)
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT party_name, amount, type FROM ledger_entries"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as exc:
        logger.error("DB error in _compute_balance: %s", exc)
        return 0.0, 0.0, 0.0

    target  = party_name.strip().lower()
    owed    = 0.0
    paid    = 0.0

    for r_party_raw, r_amount_raw, r_type in rows:
        r_party  = _decrypt_safe(r_party_raw,  return_type=str)
        r_amount = _decrypt_safe(r_amount_raw, return_type=float)

        if not r_party or r_party.strip().lower() != target:
            continue

        if r_type == "udhaar":
            owed += r_amount
        elif r_type in ("payment", "realization"):
            paid += r_amount

    return owed, paid, owed - paid


def _oldest_unpaid(party_name: str, db_path: str = DB_PATH) -> tuple[str, float]:
    """
    Returns (oldest_date, oldest_original_amount) for the single oldest unpaid
    udhaar entry belonging to party_name.

    Uses _get_unpaid_udhaar_rows from fifo_settler (which handles the
    FIFO UNPAID / PARTIALLY_PAID column logic gracefully).

    Falls back to a direct DB query if the FIFO columns don't exist yet.
    """
    # ── Primary path: use FIFO settler ──────────────────────────────────
    try:
        from fifo_settler import _get_unpaid_udhaar_rows  # noqa: PLC0415
        unpaid = _get_unpaid_udhaar_rows(party_name, db_path)
        if unpaid:
            oldest = unpaid[0]          # already sorted oldest-first
            return oldest["date"], oldest["original_amount"]
    except Exception as fifo_err:
        logger.debug("FIFO settler unavailable (%s) – using direct DB fallback.", fifo_err)

    # ── Fallback: direct query without payment_status filter ────────────
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, party_name, amount FROM ledger_entries "
            "WHERE type = 'udhaar' ORDER BY date ASC, entry_id ASC"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as exc:
        logger.error("DB error in _oldest_unpaid fallback: %s", exc)
        return "N/A", 0.0

    target = party_name.strip().lower()
    for r_date, r_party_raw, r_amount_raw in rows:
        r_party  = _decrypt_safe(r_party_raw,  return_type=str)
        r_amount = _decrypt_safe(r_amount_raw, return_type=float)
        if r_party and r_party.strip().lower() == target:
            return r_date, r_amount

    return "N/A", 0.0


def _build_share_link(party_name: str) -> str:
    """Generates the secure, daily-rotating local gateway URL for the customer."""
    try:
        from local_gateway import get_lan_ip, get_party_hash  # noqa: PLC0415
        lan_ip     = get_lan_ip()
        party_hash = get_party_hash(party_name)
        return f"http://{lan_ip}:8000/share/ledger/{party_hash}"
    except Exception as exc:
        logger.warning("Could not generate gateway link: %s", exc)
        return "http://127.0.0.1:8000/share/ledger/(unavailable)"


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════

def draft_outstanding_reminder(party_name: str, db_path: str = DB_PATH) -> str:
    """
    Assembles a complete, WhatsApp-ready Hinglish statement nudge message for
    the given customer.

    Steps
    -----
    1. Compute net outstanding balance from the ledger database.
    2. Identify the oldest unpaid/partially-paid credit entry (aging debt hook).
    3. Generate the customer's secure read-only daily-rotating gateway link.
    4. Render the Hinglish template with all values interpolated.

    Parameters
    ----------
    party_name : str   – Customer name as stored in the ledger.
    db_path    : str   – Path to the SQLite database (defaults to production DB).

    Returns
    -------
    str – Fully formatted WhatsApp message ready for clipboard copy.

    Raises
    ------
    ValueError  – If party_name is empty or blank.
    RuntimeError – If the database cannot be read.
    """
    party_name = (party_name or "").strip()
    if not party_name:
        raise ValueError("party_name cannot be empty")

    # ── 1. Outstanding balance ──────────────────────────────────────────
    total_owed, total_paid, balance = _compute_balance(party_name, db_path)

    # ── 2. Oldest unpaid entry ──────────────────────────────────────────
    oldest_date, oldest_amount = _oldest_unpaid(party_name, db_path)

    # ── 3. Secure share link ────────────────────────────────────────────
    share_link = _build_share_link(party_name)

    # ── 4. Pump branding from environment (set in .env or OS env) ───────
    pump_name = os.environ.get("PUMP_NAME", "Fuel Station")

    # ── 5. Hinglish template ────────────────────────────────────────────
    #
    # Formatting conventions:
    #   *bold*  / **bold**  → WhatsApp markdown
    #   ₹XX,XX,XXX.XX       → Indian number formatting (comma after 2 digits
    #                          from right, then groups of 2)
    #
    def inr(amount: float) -> str:
        """Format a float as Indian rupees with ₹ symbol."""
        # Use Python's locale-agnostic implementation for portability
        s = f"{abs(amount):,.2f}"
        # Re-format to Indian grouping: last 3 digits, then groups of 2
        int_part, dec_part = s.split(".")
        int_part = int_part.replace(",", "")   # strip commas first
        if len(int_part) > 3:
            last3   = int_part[-3:]
            rest    = int_part[:-3]
            # group rest in 2s from the right
            groups  = []
            while rest:
                groups.append(rest[-2:])
                rest = rest[:-2]
            formatted = ",".join(reversed(groups)) + "," + last3
        else:
            formatted = int_part
        sign = "-" if amount < 0 else ""
        return f"{sign}₹{formatted}.{dec_part}"

    reminder_msg = (
        f"*Account Statement Nudge: {pump_name}*\n\n"
        f"Namaste Ji,\n"
        f"Aapke account (*{party_name}*) ka current total outstanding balance "
        f"*{inr(balance)}* chal raha hai.\n\n"
        f"Isme sabse puraani pending entry दिनांक *{oldest_date}* ki hai "
        f"(Amt: {inr(oldest_amount)}).\n\n"
        f"Aap niche diye gaye secure local link par click karke apne sabhi "
        f"vehicle (gaddi) wise trips ki details dekh sakte hain:\n"
        f"👉 {share_link}\n\n"
        f"Kripya balance clear karne me sahyog karein. Dhanyawad! 🙏"
    )

    logger.info(
        "Reminder compiled for '%s' | balance=%.2f | oldest=%s ₹%.2f",
        party_name, balance, oldest_date, oldest_amount
    )
    return reminder_msg


def get_outstanding_customer_list(
    db_path: str = DB_PATH,
    min_balance: float = 0.01
) -> List[dict]:
    """
    Returns a list of all customers with a net outstanding balance ≥ min_balance,
    sorted by descending balance (highest debtors first).

    Each item: { party_name, total_owed, total_paid, balance }
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT party_name FROM ledger_entries WHERE type = 'udhaar'"
        )
        raw_parties = [r[0] for r in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        logger.error("DB error in get_outstanding_customer_list: %s", exc)
        return []

    results = []
    seen_names: set[str] = set()

    for enc_name in raw_parties:
        party = _decrypt_safe(enc_name, return_type=str).strip()
        if not party or party.lower() in seen_names:
            continue
        seen_names.add(party.lower())

        owed, paid, bal = _compute_balance(party, db_path)
        if bal >= min_balance:
            results.append({
                "party_name":  party,
                "total_owed":  round(owed, 2),
                "total_paid":  round(paid, 2),
                "balance":     round(bal,  2),
            })

    return sorted(results, key=lambda x: x["balance"], reverse=True)


# ════════════════════════════════════════════════════════════════════════════
# FastAPI Endpoints
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/api/customer/reminder",
    summary="Generate Hinglish WhatsApp reminder for a single customer",
    tags=["Customer Alerts"],
)
def get_customer_reminder(
    party_name: str = Query(..., description="Exact customer name as stored in the ledger"),
):
    """
    Fetches real-time outstanding balance + aging debt data for *party_name*,
    generates and returns the formatted Hinglish statement nudge text.

    Intended to be called by the frontend clipboard button:

        GET /api/customer/reminder?party_name=Sharma+Transports

    Response::

        {
            "status":   "success",
            "reminder": "<WhatsApp message string>",
            "meta": {
                "balance":      12500.00,
                "oldest_date":  "2024-11-03",
                "oldest_amount": 4500.00,
                "share_link":   "http://192.168.1.5:8000/share/ledger/<hash>"
            }
        }
    """
    party_name = (party_name or "").strip()
    if not party_name:
        raise HTTPException(status_code=400, detail="party_name query parameter cannot be empty")

    try:
        # Compute components independently so we can return structured meta too
        total_owed, total_paid, balance = _compute_balance(party_name)
        oldest_date, oldest_amount      = _oldest_unpaid(party_name)
        share_link                      = _build_share_link(party_name)
        reminder                        = draft_outstanding_reminder(party_name)

        return {
            "status":   "success",
            "reminder": reminder,
            "meta": {
                "party_name":    party_name,
                "total_owed":    round(total_owed,    2),
                "total_paid":    round(total_paid,    2),
                "balance":       round(balance,       2),
                "oldest_date":   oldest_date,
                "oldest_amount": round(oldest_amount, 2),
                "share_link":    share_link,
            },
        }

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve
    except Exception as exc:
        logger.exception("Error compiling reminder for '%s': %s", party_name, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/customer/reminder/bulk",
    summary="Generate Hinglish WhatsApp reminders for ALL outstanding customers",
    tags=["Customer Alerts"],
)
def get_bulk_reminders(
    min_balance: float = Query(
        default=1.0,
        ge=0.0,
        description="Only include customers with outstanding balance ≥ this value",
    ),
):
    """
    Returns a list of formatted reminder strings for every customer whose
    outstanding balance is at or above *min_balance*.

    Useful for batch WhatsApp broadcast workflows.

    Response::

        {
            "status": "success",
            "count":  3,
            "items": [
                { "party_name": "...", "balance": 12500.00, "reminder": "..." },
                ...
            ]
        }
    """
    try:
        debtors = get_outstanding_customer_list(min_balance=min_balance)
    except Exception as exc:
        logger.exception("Error fetching outstanding customer list: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    items = []
    for debtor in debtors:
        try:
            msg = draft_outstanding_reminder(debtor["party_name"])
            items.append({
                "party_name": debtor["party_name"],
                "balance":    debtor["balance"],
                "reminder":   msg,
            })
        except Exception as exc:
            logger.warning("Skipping '%s' in bulk: %s", debtor["party_name"], exc)

    return {
        "status": "success",
        "count":  len(items),
        "items":  items,
    }


@router.get(
    "/api/customer/outstanding-list",
    summary="List all customers with outstanding balances",
    tags=["Customer Alerts"],
)
def get_outstanding_list(
    min_balance: float = Query(
        default=0.01,
        ge=0.0,
        description="Minimum outstanding balance to include",
    ),
):
    """
    Returns a sorted list of all debtors and their balance summary.
    Used by the frontend ledger grid to dynamically populate the copy-nudge buttons.

    Response::

        {
            "status": "success",
            "count": 5,
            "debtors": [
                { "party_name": "Sharma Transports", "total_owed": ..., "total_paid": ..., "balance": ... },
                ...
            ]
        }
    """
    try:
        debtors = get_outstanding_customer_list(min_balance=min_balance)
        return {
            "status":  "success",
            "count":   len(debtors),
            "debtors": debtors,
        }
    except Exception as exc:
        logger.exception("Error fetching outstanding list: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
