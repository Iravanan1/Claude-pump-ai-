#!/usr/bin/env python3
"""
dip_profiler.py
───────────────
Underground Fuel Tank Tilt Profile Adjuster and Error-Logging Calibration Module.

Physical Background
───────────────────
An underground storage tank (UST) resting on slightly non-level ground develops
a systematic, depth-dependent volume reading error.  The factory calibration chart
was produced assuming a perfectly level tank.  When the tank has a structural tilt:

    • At high fill levels (tank nearly full)  → chart volume ≈ actual volume
    • At low fill levels (tank near empty)    → chart volume deviates from actual

This is the signature pattern: mismatch is correlated with dip depth (mm) but
meter sales and arithmetic checks are simultaneously verified as exact.  The error
is therefore NOT a theft / evaporation / meter fault — it is a physical geometry
artefact of the tank's installed tilt angle.

Correction Model
────────────────
We fit a simple linear model:

    chart_volume(mm)  +  tilt_coeff × mm  ≈  true_volume(mm)
    Corrected Volume  =  chart_volume(mm) + (mm × tilt_coefficient)

The tilt_coefficient (litres per mm of depth) is estimated by Ordinary Least
Squares on 90 days of (mm_depth, variance_liters) pairs where:

    variance_liters  =  closing_dip_liters_actual  −  expected_book_liters

If the meter checks are clean (cash & meter perfectly balanced), the entire
variance originates from the dip reading error, which this model corrects.

DB Tables Created / Used
────────────────────────
    tank_dip_log            — raw mm readings per tank per date (written here)
    tank_tilt_profiles      — per-tank OLS coefficients + metadata
    dip_calibration_events  — audit log of every correction run

FastAPI Endpoints Registered
────────────────────────────
    POST /api/dip-log/record            Record a raw mm dip reading
    GET  /api/dip-profiler/analyze/{tank_id}   Run drift analysis
    GET  /api/dip-profiler/profile/{tank_id}   Read current tilt profile
    POST /api/dip-profiler/apply/{tank_id}     Force a recalculation run
    GET  /api/dip-profiler/events              Last N calibration events
"""

import os
import sqlite3
import logging
import math
from datetime import date, timedelta
from typing import List, Optional, Tuple, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("DipProfiler")

# ── Paths ────────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BACKEND_DIR, "ledger.db")

router = APIRouter(tags=["Dip Profiler"])

# ── Configuration ────────────────────────────────────────────────────────────

# Minimum number of qualifying data points before OLS is computed.
# Below this, the module logs a warning and keeps the existing coefficient.
OLS_MIN_POINTS: int = 10

# Maximum absolute tilt coefficient that is physically plausible
# (litres per mm).  Coefficients outside this range are rejected as outliers.
PLAUSIBLE_COEFF_ABS_MAX: float = 5.0   # litres / mm

# Variance magnitude threshold below which a day is considered "clean" and
# excluded from regression (avoids fitting on genuinely correct readings).
VARIANCE_NOISE_FLOOR_LITERS: float = 0.5

# Minimum fraction of days in the analysis window that must show a
# *directional* pattern (all positive or all negative residuals in low-dip
# zone) for the anomaly to be classified as a structural tilt vs random noise.
DIRECTIONAL_CONSISTENCY_THRESHOLD: float = 0.65   # 65%

# "Low dip zone" defined as below this fraction of the maximum recorded dip.
LOW_DIP_ZONE_FRACTION: float = 0.40   # bottom 40% of tank height

# Analysis window in days
ANALYSIS_WINDOW_DAYS: int = 90


# ════════════════════════════════════════════════════════════════════════════
# 1.  DATABASE SCHEMA
# ════════════════════════════════════════════════════════════════════════════

def init_dip_profiler_db(db_path: str = DB_PATH) -> None:
    """
    Creates the three tables used by this module if they do not exist.

    Tables
    ------
    tank_dip_log
        Primary data source.  Stores a raw mm dip reading alongside the
        simultaneous meter-verified variance so the OLS regression has clean
        (x, y) pairs.

    tank_tilt_profiles
        Per-tank OLS output.  The tilt_coefficient here is what gets injected
        into convert_dip_to_liters() at runtime.

    dip_calibration_events
        Immutable audit trail of every analysis run.
    """
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── tank_dip_log ─────────────────────────────────────────────────────
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tank_dip_log (
        log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tank_id          TEXT    NOT NULL,
        reading_date     TEXT    NOT NULL,          -- YYYY-MM-DD
        dip_mm           REAL    NOT NULL,          -- raw stick/gauge reading
        chart_volume_L   REAL,                      -- factory chart value at this mm
        actual_variance_L REAL,                     -- closing_actual - expected_book
        meter_check_ok   INTEGER DEFAULT 1,         -- 1=meter balanced, 0=discrepancy
        source           TEXT    DEFAULT 'manual',  -- 'manual' | 'api' | 'auto'
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tank_id, reading_date)
    )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_dip_log_tank_date "
        "ON tank_dip_log (tank_id, reading_date)"
    )

    # ── tank_tilt_profiles ────────────────────────────────────────────────
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tank_tilt_profiles (
        tank_id              TEXT PRIMARY KEY,
        tilt_coefficient     REAL    DEFAULT 0.0,   -- L per mm of depth
        ols_intercept        REAL    DEFAULT 0.0,   -- L at mm=0 (expected ~0)
        r_squared            REAL    DEFAULT 0.0,   -- goodness-of-fit (0–1)
        n_points             INTEGER DEFAULT 0,     -- regression sample size
        analysis_start_date  TEXT,                  -- window start (YYYY-MM-DD)
        analysis_end_date    TEXT,                  -- window end
        anomaly_type         TEXT    DEFAULT 'none',-- 'none' | 'tilt' | 'noise'
        anomaly_confidence   REAL    DEFAULT 0.0,   -- 0.0 – 1.0
        correction_active    INTEGER DEFAULT 0,     -- 1 = applied at runtime
        last_updated         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── dip_calibration_events ────────────────────────────────────────────
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dip_calibration_events (
        event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tank_id          TEXT    NOT NULL,
        event_type       TEXT    NOT NULL, -- 'analysis' | 'correction_applied' | 'correction_reset'
        tilt_coefficient REAL,
        r_squared        REAL,
        n_points         INTEGER,
        anomaly_type     TEXT,
        anomaly_confidence REAL,
        notes            TEXT,
        occurred_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()
    logger.info("DipProfiler DB tables initialised.")


# ════════════════════════════════════════════════════════════════════════════
# 2.  ORDINARY LEAST SQUARES (pure Python, no scipy/numpy dependency)
# ════════════════════════════════════════════════════════════════════════════

def _ols_fit(x_vals: List[float], y_vals: List[float]) -> Tuple[float, float, float]:
    """
    Fits y = slope * x + intercept to (x_vals, y_vals) using exact OLS formulae.

    Returns
    -------
    (slope, intercept, r_squared)
        slope      — tilt coefficient  (litres per mm)
        intercept  — y-axis offset     (litres at mm=0, expect ≈ 0)
        r_squared  — coefficient of determination (0 = no fit, 1 = perfect)

    Raises
    ------
    ValueError if fewer than 2 points or zero variance in x.
    """
    n = len(x_vals)
    if n < 2:
        raise ValueError(f"OLS requires ≥ 2 points; got {n}.")

    sum_x  = sum(x_vals)
    sum_y  = sum(y_vals)
    sum_xy = sum(xi * yi for xi, yi in zip(x_vals, y_vals))
    sum_x2 = sum(xi ** 2 for xi in x_vals)

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-12:
        raise ValueError("Zero variance in x_vals; cannot fit OLS.")

    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R²
    y_mean   = sum_y / n
    ss_tot   = sum((yi - y_mean) ** 2 for yi in y_vals)
    ss_res   = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x_vals, y_vals))
    r_sq     = 1.0 - (ss_res / ss_tot) if abs(ss_tot) > 1e-12 else 0.0

    return slope, intercept, max(0.0, min(1.0, r_sq))


# ════════════════════════════════════════════════════════════════════════════
# 3.  DATA COLLECTION HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _fetch_analysis_rows(
    tank_id: str,
    db_path: str = DB_PATH,
    window_days: int = ANALYSIS_WINDOW_DAYS,
) -> List[Dict[str, Any]]:
    """
    Assembles the (dip_mm, variance_L) time series for OLS from tank_dip_log.

    Only includes rows where:
      • meter_check_ok = 1   (meter arithmetic is verified clean)
      • |actual_variance_L| ≥ VARIANCE_NOISE_FLOOR_LITERS  (non-trivial error)
      • dip_mm > 0

    Falls back to reconstructing the variance from stock_recon when
    tank_dip_log rows have null actual_variance_L (backwards compatibility).
    """
    start_date = (date.today() - timedelta(days=window_days)).isoformat()
    end_date   = date.today().isoformat()

    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── Primary: use tank_dip_log if populated ────────────────────────────
    cursor.execute("""
        SELECT reading_date, dip_mm, chart_volume_L, actual_variance_L
        FROM tank_dip_log
        WHERE tank_id = ?
          AND reading_date BETWEEN ? AND ?
          AND meter_check_ok = 1
          AND dip_mm > 0
        ORDER BY reading_date ASC
    """, (tank_id, start_date, end_date))
    log_rows = cursor.fetchall()

    rows_out: List[Dict[str, Any]] = []

    for r_date, dip_mm, chart_L, variance_L in log_rows:
        if variance_L is None:
            continue
        if abs(variance_L) < VARIANCE_NOISE_FLOOR_LITERS:
            continue
        rows_out.append({
            "date":      r_date,
            "dip_mm":    float(dip_mm),
            "chart_L":   float(chart_L) if chart_L is not None else None,
            "variance_L": float(variance_L),
        })

    conn.close()

    # ── Fallback: reconstruct from stock_recon ────────────────────────────
    # stock_recon stores liters only (not raw mm), so we cannot recover mm
    # from it.  We note this limitation and return what we have.
    if not rows_out:
        logger.warning(
            "tank_dip_log has no qualifying rows for tank '%s' in window [%s … %s]. "
            "Populate tank_dip_log via POST /api/dip-log/record to enable tilt analysis.",
            tank_id, start_date, end_date,
        )

    return rows_out


# ════════════════════════════════════════════════════════════════════════════
# 4.  VARIANCE PATTERN CLASSIFIER
# ════════════════════════════════════════════════════════════════════════════

def _classify_anomaly(
    rows: List[Dict[str, Any]],
    all_dip_mm: List[float],
) -> Tuple[str, float]:
    """
    Determines whether the variance pattern is consistent with structural tilt.

    Tilt signature rules (must ALL be true):
      1. Variance correlates with dip depth — low-dip readings show larger
         absolute variance than high-dip readings.
      2. The variance in the low-dip zone is *directionally consistent*
         (always positive OR always negative — not random).
      3. The pattern is NOT explained by meter errors (caller already filters
         meter_check_ok = 1).

    Returns
    -------
    (anomaly_type, confidence)
        anomaly_type: 'tilt' | 'noise' | 'insufficient_data'
        confidence  : 0.0 – 1.0
    """
    if len(rows) < OLS_MIN_POINTS:
        return ("insufficient_data", 0.0)

    max_mm = max(all_dip_mm) if all_dip_mm else 1.0
    low_cutoff = max_mm * LOW_DIP_ZONE_FRACTION

    low_zone_variances  = [r["variance_L"] for r in rows if r["dip_mm"] <= low_cutoff]
    high_zone_variances = [r["variance_L"] for r in rows if r["dip_mm"] >  low_cutoff]

    if not low_zone_variances:
        return ("noise", 0.0)

    # ── Directional consistency in low zone ──────────────────────────────
    n_pos      = sum(1 for v in low_zone_variances if v > 0)
    n_neg      = sum(1 for v in low_zone_variances if v < 0)
    n_low      = len(low_zone_variances)
    direction  = max(n_pos, n_neg) / n_low if n_low > 0 else 0.0

    if direction < DIRECTIONAL_CONSISTENCY_THRESHOLD:
        return ("noise", direction)

    # ── Magnitude: low zone should have larger absolute variance ─────────
    mean_abs_low  = sum(abs(v) for v in low_zone_variances)  / max(len(low_zone_variances),  1)
    mean_abs_high = sum(abs(v) for v in high_zone_variances) / max(len(high_zone_variances), 1)

    magnitude_ok = mean_abs_low > mean_abs_high * 1.5   # low zone 50% worse

    # ── Confidence score ──────────────────────────────────────────────────
    confidence = direction
    if magnitude_ok:
        confidence = min(1.0, confidence * 1.20)   # boost for depth-correlation

    if confidence >= DIRECTIONAL_CONSISTENCY_THRESHOLD and magnitude_ok:
        return ("tilt", round(confidence, 4))
    else:
        return ("noise", round(confidence, 4))


# ════════════════════════════════════════════════════════════════════════════
# 5.  MAIN ANALYSIS FUNCTION
# ════════════════════════════════════════════════════════════════════════════

def analyze_calibration_drift(
    tank_id: str,
    db_path: str = DB_PATH,
    window_days: int = ANALYSIS_WINDOW_DAYS,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Reads the past `window_days` of dip + variance data for `tank_id`,
    classifies the variance pattern, and computes a linear tilt correction
    coefficient via OLS regression.

    Algorithm
    ---------
    1. Fetch (dip_mm, variance_L) pairs from tank_dip_log where meter checks
       are verified clean and variance > noise floor.
    2. Classify the pattern: tilt signature vs random noise.
    3. Fit OLS: variance_L ~ β₁·dip_mm + β₀.
       The slope β₁ is the tilt_coefficient (litres / mm error per mm depth).
    4. Validate: reject coefficient if |β₁| > PLAUSIBLE_COEFF_ABS_MAX.
    5. Persist results to tank_tilt_profiles and dip_calibration_events.
    6. Return a structured analysis report.

    Parameters
    ----------
    tank_id    : str  — Tank identifier (must match tank_calibration_charts)
    db_path    : str  — SQLite path
    window_days: int  — Look-back window (default 90 days)
    persist    : bool — Write results to DB (set False for dry-run)

    Returns
    -------
    dict with keys:
        tank_id, anomaly_type, anomaly_confidence,
        tilt_coefficient, ols_intercept, r_squared, n_points,
        analysis_start, analysis_end,
        data_rows (list of {date, dip_mm, variance_L}),
        recommendation (human-readable string)
    """
    logger.info("Starting calibration drift analysis for tank '%s'.", tank_id)

    analysis_start = (date.today() - timedelta(days=window_days)).isoformat()
    analysis_end   = date.today().isoformat()

    # ── Collect data ──────────────────────────────────────────────────────
    rows = _fetch_analysis_rows(tank_id, db_path, window_days)

    if len(rows) < OLS_MIN_POINTS:
        result = {
            "tank_id":           tank_id,
            "anomaly_type":      "insufficient_data",
            "anomaly_confidence": 0.0,
            "tilt_coefficient":  0.0,
            "ols_intercept":     0.0,
            "r_squared":         0.0,
            "n_points":          len(rows),
            "analysis_start":    analysis_start,
            "analysis_end":      analysis_end,
            "data_rows":         rows,
            "recommendation":    (
                f"Only {len(rows)} qualifying data points found in the last "
                f"{window_days} days (minimum required: {OLS_MIN_POINTS}). "
                "Record more dip readings via POST /api/dip-log/record. "
                "No correction coefficient computed."
            ),
        }
        _log_event(db_path, tank_id, "analysis", result)
        return result

    # ── Classify anomaly ──────────────────────────────────────────────────
    all_mm       = [r["dip_mm"] for r in rows]
    anomaly_type, anomaly_confidence = _classify_anomaly(rows, all_mm)

    # ── OLS regression ────────────────────────────────────────────────────
    x_vals = [r["dip_mm"]    for r in rows]
    y_vals = [r["variance_L"] for r in rows]

    try:
        slope, intercept, r_sq = _ols_fit(x_vals, y_vals)
    except ValueError as ols_err:
        logger.error("OLS fitting failed for tank '%s': %s", tank_id, ols_err)
        slope, intercept, r_sq = 0.0, 0.0, 0.0

    # ── Plausibility gate ─────────────────────────────────────────────────
    coeff_valid = abs(slope) <= PLAUSIBLE_COEFF_ABS_MAX
    if not coeff_valid:
        logger.warning(
            "Tank '%s': computed tilt coefficient %.4f L/mm exceeds plausibility "
            "limit ±%.1f L/mm. Clamping to 0 (no correction applied).",
            tank_id, slope, PLAUSIBLE_COEFF_ABS_MAX,
        )
        slope = 0.0

    # ── Build recommendation string ───────────────────────────────────────
    if anomaly_type == "tilt" and coeff_valid and abs(slope) > 1e-6:
        direction = "over-reporting" if slope < 0 else "under-reporting"
        recommendation = (
            f"STRUCTURAL TILT DETECTED for tank '{tank_id}'.\n"
            f"Pattern: consistently {direction} volume at low dip levels, "
            f"matching perfectly at high fill levels.\n"
            f"Tilt Coefficient: {slope:+.4f} L/mm depth "
            f"(R²={r_sq:.3f}, n={len(rows)} days).\n"
            f"Confidence: {anomaly_confidence*100:.0f}%.\n"
            f"Action: Activate correction via POST /api/dip-profiler/apply/{tank_id}. "
            f"Corrected Volume = Chart Volume(mm) + ({slope:+.4f} × mm)."
        )
    elif anomaly_type == "noise":
        recommendation = (
            f"No structural tilt detected for tank '{tank_id}'. "
            f"Variance pattern is directionally inconsistent "
            f"(confidence={anomaly_confidence*100:.0f}%). "
            "This may indicate random evaporation, measurement scatter, or "
            "intermittent meter calibration drift. No geometric correction applied."
        )
    elif anomaly_type == "insufficient_data":
        recommendation = (
            f"Insufficient data for tank '{tank_id}': "
            f"{len(rows)} points (need {OLS_MIN_POINTS}+)."
        )
    else:
        recommendation = (
            f"Analysis complete for tank '{tank_id}'. "
            f"Anomaly type: {anomaly_type}. "
            f"Coefficient: {slope:+.4f} L/mm (R²={r_sq:.3f})."
        )

    result = {
        "tank_id":            tank_id,
        "anomaly_type":       anomaly_type,
        "anomaly_confidence": anomaly_confidence,
        "tilt_coefficient":   round(slope,     6),
        "ols_intercept":      round(intercept, 6),
        "r_squared":          round(r_sq,      6),
        "n_points":           len(rows),
        "analysis_start":     analysis_start,
        "analysis_end":       analysis_end,
        "data_rows":          rows,
        "recommendation":     recommendation,
    }

    # ── Persist ───────────────────────────────────────────────────────────
    if persist:
        _upsert_profile(db_path, tank_id, result)
        _log_event(db_path, tank_id, "analysis", result)

    logger.info(
        "Analysis complete for tank '%s': %s (coeff=%+.4f, R²=%.3f, n=%d)",
        tank_id, anomaly_type, slope, r_sq, len(rows),
    )
    return result


# ════════════════════════════════════════════════════════════════════════════
# 6.  PROFILE READ / WRITE HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _upsert_profile(db_path: str, tank_id: str, result: Dict[str, Any]) -> None:
    """Write OLS results into tank_tilt_profiles (upsert by tank_id)."""
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tank_tilt_profiles
            (tank_id, tilt_coefficient, ols_intercept, r_squared, n_points,
             analysis_start_date, analysis_end_date,
             anomaly_type, anomaly_confidence, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(tank_id) DO UPDATE SET
            tilt_coefficient    = excluded.tilt_coefficient,
            ols_intercept       = excluded.ols_intercept,
            r_squared           = excluded.r_squared,
            n_points            = excluded.n_points,
            analysis_start_date = excluded.analysis_start_date,
            analysis_end_date   = excluded.analysis_end_date,
            anomaly_type        = excluded.anomaly_type,
            anomaly_confidence  = excluded.anomaly_confidence,
            last_updated        = CURRENT_TIMESTAMP
    """, (
        tank_id,
        result["tilt_coefficient"],
        result["ols_intercept"],
        result["r_squared"],
        result["n_points"],
        result["analysis_start"],
        result["analysis_end"],
        result["anomaly_type"],
        result["anomaly_confidence"],
    ))
    conn.commit()
    conn.close()


def _log_event(db_path: str, tank_id: str, event_type: str, result: Dict[str, Any], notes: str = "") -> None:
    """Append an immutable audit row to dip_calibration_events."""
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dip_calibration_events
                (tank_id, event_type, tilt_coefficient, r_squared, n_points,
                 anomaly_type, anomaly_confidence, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tank_id,
            event_type,
            result.get("tilt_coefficient"),
            result.get("r_squared"),
            result.get("n_points"),
            result.get("anomaly_type"),
            result.get("anomaly_confidence"),
            notes or result.get("recommendation", ""),
        ))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Failed to write calibration event for tank '%s': %s", tank_id, exc)


def get_tilt_profile(tank_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """
    Reads the current tilt profile for a tank from tank_tilt_profiles.

    Returns None if no profile has been computed yet.
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tilt_coefficient, ols_intercept, r_squared, n_points,
                   analysis_start_date, analysis_end_date,
                   anomaly_type, anomaly_confidence,
                   correction_active, last_updated
            FROM tank_tilt_profiles
            WHERE tank_id = ?
        """, (tank_id,))
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None

        return {
            "tank_id":            tank_id,
            "tilt_coefficient":   row[0],
            "ols_intercept":      row[1],
            "r_squared":          row[2],
            "n_points":           row[3],
            "analysis_start":     row[4],
            "analysis_end":       row[5],
            "anomaly_type":       row[6],
            "anomaly_confidence": row[7],
            "correction_active":  bool(row[8]),
            "last_updated":       row[9],
        }
    except Exception as exc:
        logger.error("Failed to read tilt profile for tank '%s': %s", tank_id, exc)
        return None


def get_tilt_coefficient(tank_id: str, db_path: str = DB_PATH) -> float:
    """
    Returns the active tilt correction coefficient for `tank_id`.

    Called at runtime by convert_dip_to_liters() (interceptor hook).
    Returns 0.0 if:
      • No profile exists for this tank.
      • correction_active = 0 (disabled by operator).
      • anomaly_type != 'tilt'.
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tilt_coefficient, correction_active, anomaly_type
            FROM tank_tilt_profiles
            WHERE tank_id = ?
        """, (tank_id,))
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return 0.0
        coeff, active, atype = row
        if not active or atype != "tilt":
            return 0.0
        return float(coeff or 0.0)

    except Exception:
        return 0.0   # fail-safe: never raise from a runtime correction lookup


def set_correction_active(
    tank_id:  str,
    enabled:  bool,
    db_path:  str = DB_PATH,
) -> None:
    """
    Enables or disables the tilt correction for a tank at runtime.
    Persists the change to tank_tilt_profiles and audit-logs it.
    """
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE tank_tilt_profiles
        SET correction_active = ?, last_updated = CURRENT_TIMESTAMP
        WHERE tank_id = ?
    """, (1 if enabled else 0, tank_id))
    conn.commit()
    conn.close()

    event_type = "correction_applied" if enabled else "correction_reset"
    profile = get_tilt_profile(tank_id, db_path) or {}
    _log_event(db_path, tank_id, event_type, profile,
               notes=f"Correction {'ENABLED' if enabled else 'DISABLED'} by operator.")
    logger.info("Tank '%s' tilt correction %s.", tank_id, "ENABLED" if enabled else "DISABLED")


# ════════════════════════════════════════════════════════════════════════════
# 7.  DIP LOG RECORDING
# ════════════════════════════════════════════════════════════════════════════

def record_dip_reading(
    tank_id:           str,
    reading_date:      str,
    dip_mm:            float,
    actual_variance_L: float,
    meter_check_ok:    bool  = True,
    source:            str   = "api",
    db_path:           str   = DB_PATH,
) -> int:
    """
    Records a single raw dip measurement + simultaneous variance into tank_dip_log.

    The variance_L should be:
        (actual closing stock measured by dip) - (expected closing book stock)

    where expected = opening_stock + receipts - meter_sales.
    When meter arithmetic is verified (meter_check_ok=True), any non-zero
    variance is entirely attributable to the dip reading error (tilt or sensor).

    Returns the log_id of the inserted row.
    """
    from tank_calibration import convert_dip_to_liters  # noqa: PLC0415

    # Factory chart volume at this depth (without any tilt correction)
    # We temporarily bypass the tilt hook by reading raw from the chart.
    chart_L = _raw_chart_volume(tank_id, dip_mm, db_path)

    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO tank_dip_log
            (tank_id, reading_date, dip_mm, chart_volume_L,
             actual_variance_L, meter_check_ok, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tank_id, reading_date) DO UPDATE SET
            dip_mm            = excluded.dip_mm,
            chart_volume_L    = excluded.chart_volume_L,
            actual_variance_L = excluded.actual_variance_L,
            meter_check_ok    = excluded.meter_check_ok,
            source            = excluded.source
    """, (
        tank_id, reading_date, dip_mm, chart_L,
        actual_variance_L, 1 if meter_check_ok else 0, source,
    ))
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()

    logger.info(
        "Dip log recorded: tank=%s date=%s mm=%.1f variance=%.3fL meter_ok=%s",
        tank_id, reading_date, dip_mm, actual_variance_L, meter_check_ok,
    )
    return log_id


def _raw_chart_volume(tank_id: str, dip_mm: float, db_path: str = DB_PATH) -> Optional[float]:
    """
    Returns the factory-chart volume at `dip_mm` WITHOUT applying any tilt
    correction.  Used internally so the dip_log stores clean chart values.
    """
    try:
        conn   = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dip_level_mm, volume_liters
            FROM tank_calibration_charts
            WHERE tank_id = ?
            ORDER BY dip_level_mm ASC
        """, (tank_id,))
        points = cursor.fetchall()
        conn.close()

        if not points:
            return None

        # Exact match
        for x, y in points:
            if abs(x - dip_mm) < 1e-6:
                return float(y)

        x_min, y_min = points[0]
        x_max, y_max = points[-1]

        if dip_mm < x_min:
            return float(dip_mm * y_min / x_min) if x_min > 0 else float(y_min)
        if dip_mm > x_max:
            return float(y_max)

        # Linear interpolation
        for i in range(len(points) - 1):
            x0, y0 = points[i]
            x1, y1 = points[i + 1]
            if x0 < dip_mm < x1:
                return float(y0 + (dip_mm - x0) * (y1 - y0) / (x1 - x0))

        return None
    except Exception as exc:
        logger.error("_raw_chart_volume error for tank '%s': %s", tank_id, exc)
        return None


# ════════════════════════════════════════════════════════════════════════════
# 8.  FastAPI Endpoints
# ════════════════════════════════════════════════════════════════════════════

class DipLogRequest(BaseModel):
    tank_id:           str
    reading_date:      str             # YYYY-MM-DD
    dip_mm:            float
    actual_variance_L: float           # closing_actual_L - expected_book_L
    meter_check_ok:    bool  = True
    source:            str   = "api"


class CorrectionToggleRequest(BaseModel):
    tank_id: str
    enabled: bool


@router.post(
    "/api/dip-log/record",
    summary="Record a raw dip reading + variance for tilt profiling",
)
def api_record_dip(req: DipLogRequest):
    """
    Records a raw dip measurement and the simultaneously observed inventory
    variance into `tank_dip_log` for use by the tilt drift analyser.

    **Fields**

    | Field | Description |
    |---|---|
    | tank_id | Must match a `tank_id` in `tank_calibration_charts` |
    | reading_date | YYYY-MM-DD of the reading |
    | dip_mm | Raw stick/gauge reading in millimetres |
    | actual_variance_L | `closing_dip_actual − expected_book` in litres |
    | meter_check_ok | `true` if meter arithmetic is independently verified |
    | source | `'manual'` / `'api'` / `'auto'` |
    """
    try:
        init_dip_profiler_db()
        log_id = record_dip_reading(
            tank_id=req.tank_id,
            reading_date=req.reading_date,
            dip_mm=req.dip_mm,
            actual_variance_L=req.actual_variance_L,
            meter_check_ok=req.meter_check_ok,
            source=req.source,
        )
        return {"status": "success", "log_id": log_id}
    except Exception as exc:
        logger.exception("Failed to record dip log: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/dip-profiler/analyze/{tank_id}",
    summary="Run calibration drift analysis for a tank",
)
def api_analyze(
    tank_id: str,
    window_days: int = Query(default=90, ge=14, le=730),
    dry_run: bool    = Query(default=False),
):
    """
    Executes the full 90-day drift analysis pipeline for `tank_id`:

    1. Fetches (mm, variance) data from `tank_dip_log`.
    2. Classifies variance pattern (tilt vs noise).
    3. Fits OLS to extract tilt coefficient.
    4. Persists results (unless `dry_run=true`).

    Returns the full analysis report including recommendation text.
    """
    try:
        init_dip_profiler_db()
        result = analyze_calibration_drift(
            tank_id=tank_id,
            window_days=window_days,
            persist=not dry_run,
        )
        return {"status": "success", **result}
    except Exception as exc:
        logger.exception("Analysis failed for tank '%s': %s", tank_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/dip-profiler/profile/{tank_id}",
    summary="Read the current tilt correction profile for a tank",
)
def api_get_profile(tank_id: str):
    """
    Returns the stored tilt coefficient, R², anomaly classification, and
    whether the runtime correction is currently active for `tank_id`.
    """
    init_dip_profiler_db()
    profile = get_tilt_profile(tank_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"No tilt profile found for tank '{tank_id}'. "
                   "Run POST /api/dip-profiler/apply/{tank_id} or "
                   "GET /api/dip-profiler/analyze/{tank_id} first.",
        )
    return {"status": "success", **profile}


@router.post(
    "/api/dip-profiler/apply/{tank_id}",
    summary="Re-run analysis and activate/deactivate tilt correction",
)
def api_apply_correction(
    tank_id: str,
    enabled: bool = Query(
        default=True,
        description="Set true to activate correction, false to disable",
    ),
):
    """
    Forces a fresh analysis run for `tank_id` and then activates or
    deactivates the runtime tilt correction in `convert_dip_to_liters()`.

    When enabled, every subsequent call to `convert_dip_to_liters(tank_id, mm)`
    will apply:

        Corrected Volume = Chart Volume(mm) + (mm × tilt_coefficient)
    """
    try:
        init_dip_profiler_db()
        analysis = analyze_calibration_drift(tank_id=tank_id, persist=True)
        set_correction_active(tank_id=tank_id, enabled=enabled)
        return {
            "status":   "success",
            "enabled":  enabled,
            "analysis": analysis,
        }
    except Exception as exc:
        logger.exception("apply_correction failed for tank '%s': %s", tank_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/api/dip-profiler/correction-toggle",
    summary="Enable or disable tilt correction without re-running analysis",
)
def api_toggle_correction(req: CorrectionToggleRequest):
    """
    Quickly enables or disables the tilt correction for `tank_id` without
    triggering a full re-analysis.  Useful for A/B testing the correction.
    """
    try:
        init_dip_profiler_db()
        set_correction_active(tank_id=req.tank_id, enabled=req.enabled)
        profile = get_tilt_profile(req.tank_id)
        return {
            "status":  "success",
            "enabled": req.enabled,
            "profile": profile,
        }
    except Exception as exc:
        logger.exception("Toggle failed for tank '%s': %s", req.tank_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/dip-profiler/events",
    summary="List recent tilt calibration audit events",
)
def api_events(
    tank_id: Optional[str] = Query(default=None),
    limit:   int           = Query(default=50, ge=1, le=500),
):
    """
    Returns the most recent `limit` rows from `dip_calibration_events`,
    optionally filtered by `tank_id`.
    """
    try:
        init_dip_profiler_db()
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if tank_id:
            cursor.execute("""
                SELECT event_id, tank_id, event_type, tilt_coefficient,
                       r_squared, n_points, anomaly_type, anomaly_confidence,
                       notes, occurred_at
                FROM dip_calibration_events
                WHERE tank_id = ?
                ORDER BY occurred_at DESC
                LIMIT ?
            """, (tank_id, limit))
        else:
            cursor.execute("""
                SELECT event_id, tank_id, event_type, tilt_coefficient,
                       r_squared, n_points, anomaly_type, anomaly_confidence,
                       notes, occurred_at
                FROM dip_calibration_events
                ORDER BY occurred_at DESC
                LIMIT ?
            """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        events = [
            {
                "event_id":          r[0],
                "tank_id":           r[1],
                "event_type":        r[2],
                "tilt_coefficient":  r[3],
                "r_squared":         r[4],
                "n_points":          r[5],
                "anomaly_type":      r[6],
                "anomaly_confidence":r[7],
                "notes":             r[8],
                "occurred_at":       r[9],
            }
            for r in rows
        ]
        return {"status": "success", "count": len(events), "events": events}

    except Exception as exc:
        logger.exception("Failed to fetch calibration events: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Auto-initialise tables on module import ──────────────────────────────────
try:
    init_dip_profiler_db()
except Exception as _init_err:
    logger.warning("DipProfiler auto-init skipped: %s", _init_err)
