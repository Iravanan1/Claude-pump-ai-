#!/usr/bin/env python3
"""
PumpAI Database Snapshot & Rollback System
==========================================
Provides:
  1. create_pre_batch_snapshot()  — Binary-copies the active production DB into
     /backend/snapshots/ with a clear timestamp+label filename before any bulk
     data-injection run begins.
  2. rollback_to_last_snapshot()  — Finds the most recent .bak file, safely
     closes all in-flight SQLite connections, replaces the active DB, and
     reinitializes FastAPI's connection pools.
  3. list_available_snapshots()   — Returns metadata for all snapshots on disk.
  4. prune_old_snapshots()        — Keeps only the N most-recent snapshots to
     prevent unbounded disk growth.

Naming template:
  snapshot_YYYYMMDD_HHMM_<label>.bak
  e.g. snapshot_20260607_2315_pre_batch.bak
"""

import os
import sys
import glob
import shutil
import sqlite3
import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

try:
    from logger import logger
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("db_rollback")

# ── Module-level state ────────────────────────────────────────────────────────

BACKEND_DIR  = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS_DIR = os.path.join(BACKEND_DIR, "snapshots")

# Maximum number of .bak snapshots to retain on disk (oldest pruned first)
MAX_SNAPSHOTS = 20

# Lock to serialise snapshot / rollback operations if called concurrently
_rollback_lock = threading.Lock()


def _ensure_snapshots_dir() -> None:
    """Create the snapshots directory if it does not already exist."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """
    Resolve the absolute path of the active production database.
    Prefers the supplied db_path argument; falls back to main.DB_PATH (if
    FastAPI is running), then to the hardcoded default 'ledger.db'.
    """
    if db_path:
        return os.path.abspath(db_path)

    try:
        import main as _main
        resolved = _main.DB_PATH
    except Exception:
        resolved = "ledger.db"

    if not os.path.isabs(resolved):
        resolved = os.path.abspath(os.path.join(BACKEND_DIR, resolved))

    return resolved


def _build_snapshot_name(label: str = "pre_batch") -> str:
    """
    Returns a timestamped .bak filename.
    Format: snapshot_YYYYMMDD_HHMM_<label>.bak
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    # Sanitise label: allow only alphanumeric + underscore + hyphen
    safe_label = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in label)
    return f"snapshot_{ts}_{safe_label}.bak"


# ── Public API ────────────────────────────────────────────────────────────────

def create_pre_batch_snapshot(
    db_path: Optional[str] = None,
    label: str = "pre_batch",
) -> str:
    """
    Session Initializer Trigger.

    Copies the active production database into /backend/snapshots/ using a
    clear, timestamp-containing filename.  Intended to be called immediately
    before any bulk backfill / batch data-injection run.

    Returns the absolute path of the newly created snapshot file.
    Raises RuntimeError if the source DB does not exist.
    """
    with _rollback_lock:
        _ensure_snapshots_dir()

        source_db = _resolve_db_path(db_path)
        if not os.path.isfile(source_db):
            raise RuntimeError(
                f"Source database not found: '{source_db}'. "
                "Cannot create snapshot before batch run."
            )

        snap_name = _build_snapshot_name(label)
        snap_path = os.path.join(SNAPSHOTS_DIR, snap_name)

        # Use SQLite's built-in Online Backup API for a consistent copy even
        # when the DB is live.  Falls back to shutil if pysqlite is old.
        try:
            _hot_copy_sqlite(source_db, snap_path)
        except Exception as backup_err:
            logger.warning(
                f"Hot SQLite backup failed ({backup_err}), "
                "falling back to file-level copy."
            )
            shutil.copy2(source_db, snap_path)

        snap_size_kb = os.path.getsize(snap_path) // 1024
        logger.info(
            f"[Snapshot] Created: '{snap_name}' "
            f"(source: '{source_db}', size: {snap_size_kb} KB)"
        )

        # Automatically prune snapshots that exceed MAX_SNAPSHOTS
        prune_old_snapshots()

        return snap_path


def _hot_copy_sqlite(source_path: str, dest_path: str) -> None:
    """
    Uses sqlite3's iterdump + Online Backup API to create a safe, consistent
    copy of a live database without requiring an exclusive lock.
    """
    src_conn  = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(dest_path)
    try:
        src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()


def list_available_snapshots() -> List[Dict[str, Any]]:
    """
    Scans the snapshots directory and returns a list of metadata dictionaries
    for each .bak file, newest first.

    Each dict contains:
      - filename   : str
      - path       : str (absolute)
      - size_bytes : int
      - created_at : str  (ISO-8601 local time derived from file mtime)
      - label      : str  (parsed from filename)
    """
    _ensure_snapshots_dir()
    pattern = os.path.join(SNAPSHOTS_DIR, "snapshot_*.bak")
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    result = []
    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            mtime = os.path.getmtime(fpath)
            created_at = datetime.fromtimestamp(mtime).isoformat()
        except OSError:
            created_at = "unknown"
        try:
            size_bytes = os.path.getsize(fpath)
        except OSError:
            size_bytes = 0

        # Parse label from filename: snapshot_YYYYMMDD_HHMM_<label>.bak
        parts = fname.replace(".bak", "").split("_", 3)
        label = parts[3] if len(parts) == 4 else "unknown"

        result.append({
            "filename":   fname,
            "path":       fpath,
            "size_bytes": size_bytes,
            "size_kb":    size_bytes // 1024,
            "created_at": created_at,
            "label":      label,
        })

    return result


def prune_old_snapshots(max_keep: int = MAX_SNAPSHOTS) -> int:
    """
    Deletes the oldest .bak snapshots beyond the `max_keep` threshold.
    Returns the number of files deleted.
    """
    _ensure_snapshots_dir()
    pattern = os.path.join(SNAPSHOTS_DIR, "snapshot_*.bak")
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    deleted = 0
    for old_file in files[max_keep:]:
        try:
            os.remove(old_file)
            logger.info(f"[Snapshot] Pruned old snapshot: '{os.path.basename(old_file)}'")
            deleted += 1
        except OSError as e:
            logger.warning(f"[Snapshot] Failed to prune '{old_file}': {e}")

    return deleted


def rollback_to_last_snapshot(
    db_path: Optional[str] = None,
    reinitialize_connections: bool = True,
) -> Dict[str, Any]:
    """
    Automated Reversion Control.

    Reverts the active production database to the most recent snapshot:
      1. Closes all active SQLite connection handles for the target DB.
      2. Locates the single most-recent .bak file.
      3. Deletes the current (corrupted / unwanted) DB file.
      4. Copies the snapshot .bak over and renames it to the active DB path.
      5. Re-initializes FastAPI connection pools / startup hooks.

    Returns a summary dict with status, snapshot used, and timing.
    Raises RuntimeError if no snapshot is available.
    """
    with _rollback_lock:
        _ensure_snapshots_dir()

        active_db = _resolve_db_path(db_path)

        # ── Step 1: Find the most-recent snapshot ─────────────────────────────
        snapshots = list_available_snapshots()
        if not snapshots:
            raise RuntimeError(
                "No snapshots found in the snapshots directory. "
                "Run create_pre_batch_snapshot() before a batch to enable rollback."
            )

        latest = snapshots[0]
        snap_path = latest["path"]
        snap_name = latest["filename"]

        logger.info(
            f"[Rollback] Starting reversion to snapshot: '{snap_name}' "
            f"(created: {latest['created_at']})"
        )

        # ── Step 2: Flush & close all open SQLite connections for this DB ─────
        _force_close_all_connections(active_db)

        # ── Step 3: Remove corrupted / unwanted DB file ────────────────────────
        if os.path.isfile(active_db):
            corrupted_backup = active_db + ".rollback_evicted"
            try:
                shutil.move(active_db, corrupted_backup)
                logger.info(
                    f"[Rollback] Evicted current DB → '{corrupted_backup}'"
                )
            except OSError as e:
                logger.error(f"[Rollback] Cannot evict current DB: {e}")
                raise RuntimeError(f"Rollback blocked: cannot remove current DB. Error: {e}")

        # ── Step 4: Restore snapshot ──────────────────────────────────────────
        try:
            shutil.copy2(snap_path, active_db)
            logger.info(f"[Rollback] Restored: '{snap_name}' → '{active_db}'")
        except OSError as e:
            logger.error(f"[Rollback] Restore copy failed: {e}")
            # Attempt to put the evicted DB back to avoid total data loss
            evicted = active_db + ".rollback_evicted"
            if os.path.isfile(evicted):
                shutil.move(evicted, active_db)
            raise RuntimeError(f"Rollback restore failed: {e}")

        # ── Step 5: Re-initialize application connection pools ────────────────
        if reinitialize_connections:
            _reinit_app_connections(active_db)

        logger.info(
            f"[Rollback] ✅ Reversion complete. "
            f"Active DB is now the snapshot from {latest['created_at']}."
        )

        return {
            "status":         "ok",
            "snapshot_used":  snap_name,
            "snapshot_size_kb": latest["size_kb"],
            "snapshot_created_at": latest["created_at"],
            "active_db":      active_db,
        }


def _force_close_all_connections(db_path: str) -> None:
    """
    Forces Python-level SQLite connection closure for the target database.
    Triggers a SQLite PRAGMA wal_checkpoint to flush WAL before evicting.
    """
    # Attempt a WAL checkpoint so we don't lose committed but un-checkpointed data
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.execute("PRAGMA optimize")
        conn.close()
        logger.info(f"[Rollback] WAL checkpoint completed on '{db_path}'")
    except Exception as e:
        logger.warning(f"[Rollback] WAL checkpoint failed (non-fatal): {e}")

    # Close the FastAPI / main module's cached connection if accessible
    try:
        import main as _main
        if hasattr(_main, "_db_conn") and _main._db_conn:
            _main._db_conn.close()
            _main._db_conn = None
            logger.info("[Rollback] Closed main._db_conn handle")
    except Exception:
        pass  # Not available in standalone mode


def _reinit_app_connections(active_db: str) -> None:
    """
    Re-establishes the FastAPI application's database connections after
    a rollback.  Calls the startup routines that main.py uses.
    """
    try:
        import main as _main
        import migrations
        from migrations import apply_schema_updates

        # Verify integrity of the restored DB
        conn = sqlite3.connect(active_db, timeout=10.0)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()

        if integrity != "ok":
            logger.error(
                f"[Rollback] Integrity check failed after restore: '{integrity}'"
            )
            raise RuntimeError(
                f"Restored DB failed integrity check: {integrity}"
            )

        logger.info(f"[Rollback] Integrity check passed: '{integrity}'")

        # Re-apply any pending schema migrations
        apply_schema_updates(active_db)
        logger.info("[Rollback] Schema migrations verified / applied.")

    except ImportError:
        logger.info(
            "[Rollback] Running in standalone mode — "
            "skipping FastAPI connection reinitialization."
        )
    except Exception as e:
        logger.error(f"[Rollback] Reinitialization error (non-fatal): {e}")
