"""
state_tracker.py — Persistent Batch Processing State Machine
=============================================================
Provides durable, crash-safe job tracking for the bulk importer.

Every image file that enters the batch pipeline gets a SHA-256 content hash
and a row in the `batch_status` table.  The row progresses through these
states in order:

    PENDING  →  PROCESSING  →  COMPLETED
                            └→  FAILED_MATH   (math validation failed)
                            └→  FAILED_OCR    (OCR / AI call failed)

On an app restart after a crash, `reset_stuck_jobs()` moves any rows that
are still in PROCESSING back to PENDING so they get retried automatically.

Public API
----------
    from state_tracker import (
        init_state_db,
        calculate_file_hash,
        get_job_status,
        is_completed,
        upsert_job,
        mark_processing,
        mark_completed,
        mark_failed,
        reset_stuck_jobs,
        get_all_jobs,
        JobStatus,
    )
"""

import os
import hashlib
import sqlite3
import logging
from enum import Enum
from datetime import datetime
from typing import Optional

try:
    from logger import logger  # type: ignore
except ImportError:
    logger = logging.getLogger("state_tracker")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BACKEND_DIR, "ledger.db")


# ---------------------------------------------------------------------------
# Status Constants
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    """Valid status values for the batch_status table."""
    PENDING              = "PENDING"
    PROCESSING           = "PROCESSING"
    COMPLETED            = "COMPLETED"
    FAILED_MATH          = "FAILED_MATH"
    FAILED_OCR           = "FAILED_OCR"
    FAILED_PREPROCESSING = "FAILED_PREPROCESSING"

    @classmethod
    def failed_statuses(cls) -> tuple:
        return (cls.FAILED_MATH.value, cls.FAILED_OCR.value, cls.FAILED_PREPROCESSING.value)

    @classmethod
    def all_values(cls) -> list[str]:
        return [s.value for s in cls]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS batch_status (
    image_hash   TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_IDX_STATUS  = "CREATE INDEX IF NOT EXISTS idx_batch_status_status   ON batch_status(status);"
_CREATE_IDX_FNAME   = "CREATE INDEX IF NOT EXISTS idx_batch_status_filename ON batch_status(file_name);"


def init_state_db(db_path: str = DB_PATH) -> None:
    """
    Creates the `batch_status` table and its indexes if they do not already
    exist.  Safe to call multiple times (idempotent).

    Args:
        db_path: Path to the SQLite database file.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            _CREATE_TABLE_SQL +
            _CREATE_IDX_STATUS  +
            _CREATE_IDX_FNAME
        )
        conn.commit()
        conn.close()
        logger.debug("state_tracker: batch_status table ready.")
    except Exception as e:
        logger.error(f"state_tracker: failed to initialise batch_status table — {e}")


# Initialise on import
init_state_db()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def calculate_file_hash(file_path: str, chunk_size: int = 65536) -> str:
    """
    Computes a SHA-256 content hash of the file at `file_path`.

    Reading in 64 KB chunks keeps memory usage flat regardless of file size.
    The hash is based purely on *content* — renaming a file does not change
    its hash, so the duplicate-skip logic correctly handles renames.

    Args:
        file_path:  Absolute path to the file.
        chunk_size: Number of bytes per read chunk (default 64 KB).

    Returns:
        str: 64-character lowercase hex digest, or "" on I/O error.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"state_tracker: file not found — '{file_path}'.")

    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                sha256.update(chunk)
        digest = sha256.hexdigest()
        logger.debug(f"state_tracker: hash({os.path.basename(file_path)}) = {digest[:12]}…")
        return digest
    except OSError as e:
        logger.error(f"state_tracker: hash failed for '{file_path}' — {e}")
        return ""


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _fetch_row(image_hash: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Returns the batch_status row for `image_hash`, or None if absent."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM batch_status WHERE image_hash = ?",
            (image_hash,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"state_tracker: _fetch_row failed — {e}")
        return None


def get_job_status(image_hash: str, db_path: str = DB_PATH) -> Optional[str]:
    """
    Returns the current status string for a given image hash, or None if the
    hash has never been registered.
    """
    row = _fetch_row(image_hash, db_path)
    return row["status"] if row else None


def is_completed(image_hash: str, db_path: str = DB_PATH) -> bool:
    """
    Returns True if and only if the file identified by `image_hash` has a
    status of COMPLETED in the database.  This is the primary skip-check
    called by bulk_importer before processing a file.
    """
    return get_job_status(image_hash, db_path) == JobStatus.COMPLETED.value


def get_all_jobs(
    status_filter: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Returns all rows from batch_status, optionally filtered by status.

    Args:
        status_filter: One of the JobStatus values, or None for all rows.
        db_path:       Path to the SQLite file.

    Returns:
        list[dict]: Rows as dictionaries ordered by updated_at DESC.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM batch_status WHERE status = ? ORDER BY updated_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM batch_status ORDER BY updated_at DESC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"state_tracker: get_all_jobs failed — {e}")
        return []


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def upsert_job(
    image_hash: str,
    file_name:  str,
    status:     str = JobStatus.PENDING.value,
    db_path:    str = DB_PATH,
) -> None:
    """
    Inserts a new job row or updates the file_name / status if the hash
    already exists.  Used when the bulk importer first encounters a file.

    If the file was previously COMPLETED, the existing row is left untouched
    so the skip-check still fires correctly.

    Args:
        image_hash: SHA-256 hex digest of the file content.
        file_name:  Original basename of the file.
        status:     Initial status (default PENDING).
        db_path:    SQLite path.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO batch_status (image_hash, file_name, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(image_hash) DO UPDATE
                SET file_name  = excluded.file_name,
                    updated_at = excluded.updated_at
                WHERE status != 'COMPLETED'
            """,
            (image_hash, file_name, status, _now()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"state_tracker: upsert_job failed for '{file_name}' — {e}")


def mark_processing(
    image_hash: str,
    db_path:    str = DB_PATH,
) -> None:
    """
    Transitions a job to PROCESSING and increments the attempts counter.
    Called immediately before the AI pipeline starts on a file.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE batch_status
            SET status     = 'PROCESSING',
                attempts   = attempts + 1,
                last_error = NULL,
                updated_at = ?
            WHERE image_hash = ?
            """,
            (_now(), image_hash),
        )
        conn.commit()
        conn.close()
        logger.debug(f"state_tracker: → PROCESSING  [{image_hash[:12]}…]")
    except Exception as e:
        logger.error(f"state_tracker: mark_processing failed — {e}")


def mark_completed(
    image_hash: str,
    db_path:    str = DB_PATH,
) -> None:
    """
    Transitions a job to COMPLETED.  Called after a successful ledger commit.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE batch_status
            SET status     = 'COMPLETED',
                last_error = NULL,
                updated_at = ?
            WHERE image_hash = ?
            """,
            (_now(), image_hash),
        )
        conn.commit()
        conn.close()
        logger.info(f"state_tracker: → COMPLETED   [{image_hash[:12]}…]")
    except Exception as e:
        logger.error(f"state_tracker: mark_completed failed — {e}")


def mark_failed(
    image_hash: str,
    reason:     str,
    is_ocr:     bool = False,
    db_path:    str = DB_PATH,
) -> None:
    """
    Transitions a job to FAILED_MATH or FAILED_OCR and records the error.

    Args:
        image_hash: SHA-256 hex digest.
        reason:     Human-readable description of the failure.
        is_ocr:     True → FAILED_OCR, False → FAILED_MATH.
        db_path:    SQLite path.
    """
    new_status = JobStatus.FAILED_OCR.value if is_ocr else JobStatus.FAILED_MATH.value
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE batch_status
            SET status     = ?,
                last_error = ?,
                updated_at = ?
            WHERE image_hash = ?
            """,
            (new_status, reason[:1000], _now(), image_hash),
        )
        conn.commit()
        conn.close()
        logger.warning(f"state_tracker: → {new_status} [{image_hash[:12]}…] — {reason[:80]}")
    except Exception as e:
        logger.error(f"state_tracker: mark_failed failed — {e}")


def mark_preprocessing_failed(
    image_hash: str,
    reason:     str,
    db_path:    str = DB_PATH,
) -> None:
    """
    Transitions a job to FAILED_PREPROCESSING and records the preprocessing error.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE batch_status
            SET status     = 'FAILED_PREPROCESSING',
                last_error = ?,
                updated_at = ?
            WHERE image_hash = ?
            """,
            (reason[:1000], _now(), image_hash),
        )
        conn.commit()
        conn.close()
        logger.warning(f"state_tracker: → FAILED_PREPROCESSING [{image_hash[:12]}…] — {reason[:80]}")
    except Exception as e:
        logger.error(f"state_tracker: mark_preprocessing_failed failed — {e}")


# ---------------------------------------------------------------------------
# Crash Recovery
# ---------------------------------------------------------------------------

def reset_stuck_jobs(db_path: str = DB_PATH) -> int:
    """
    Crash-recovery routine.  Finds all rows with status = 'PROCESSING' and
    resets them to 'PENDING' so they are picked up cleanly on the next run.

    A row is 'stuck' when the process died while the job was in-flight,
    leaving it perpetually in PROCESSING.

    Args:
        db_path: Path to the SQLite file.

    Returns:
        int: Number of rows that were reset.
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.execute(
            """
            UPDATE batch_status
            SET status     = 'PENDING',
                last_error = 'Reset by startup crash-recovery (was stuck in PROCESSING)',
                updated_at = ?
            WHERE status = 'PROCESSING'
            """,
            (_now(),),
        )
        count = cursor.rowcount
        conn.commit()
        conn.close()

        if count > 0:
            logger.warning(
                f"state_tracker: reset_stuck_jobs — recovered {count} stuck job(s) "
                "from PROCESSING → PENDING."
            )
        else:
            logger.info("state_tracker: reset_stuck_jobs — no stuck jobs found.")

        return count
    except Exception as e:
        logger.error(f"state_tracker: reset_stuck_jobs failed — {e}")
        return 0


# ---------------------------------------------------------------------------
# Stats helper (for the frontend dashboard / logging)
# ---------------------------------------------------------------------------

def get_batch_stats(db_path: str = DB_PATH) -> dict:
    """
    Returns a summary count of jobs per status.

    Returns:
        dict: {"PENDING": n, "PROCESSING": n, "COMPLETED": n,
               "FAILED_MATH": n, "FAILED_OCR": n, "total": n}
    """
    stats = {s: 0 for s in JobStatus.all_values()}
    stats["total"] = 0
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM batch_status GROUP BY status"
        ).fetchall()
        conn.close()
        for status, count in rows:
            if status in stats:
                stats[status] = count
            stats["total"] += count
    except Exception as e:
        logger.error(f"state_tracker: get_batch_stats failed — {e}")
    return stats
