"""
Tank Calibration and Dip-to-Volume Conversion Engine.

Implements database schema setup, exact and linear interpolation lookups,
and CSV loading utilities for tank dip-to-volume calculations.

Tilt Correction Interceptor
───────────────────────────
If a structural tilt profile has been computed and activated by `dip_profiler.py`,
`convert_dip_to_liters()` automatically applies a linear offset correction:

    Corrected Volume = Factory Chart Volume(mm) + (mm × localized_tilt_coefficient)

This corrects false stock-variance alarms caused by geometric shifts in older
underground storage tanks (USTs) whose tilt creates a depth-dependent volume
reporting error that cannot be fixed by recalibration alone.

The tilt coefficient is computed by `dip_profiler.analyze_calibration_drift()`
using 90 days of OLS regression on (dip_mm, observed_variance_L) pairs where
meter sales are independently verified as arithmetically exact.
"""

import os
import csv
import sqlite3
import logging

logger = logging.getLogger("TankCalibration")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")


def _apply_tilt_correction(tank_id: str, observed_mm: float, chart_volume: float, db_path: str) -> float:
    """
    Applies the localized structural tilt correction to a factory-chart volume.

    Formula
    -------
    Corrected Volume = chart_volume + (observed_mm × tilt_coefficient)

    The tilt_coefficient is stored in `tank_tilt_profiles` and is only applied
    when `correction_active = 1` and `anomaly_type = 'tilt'`.  Returns
    `chart_volume` unchanged if no active profile exists for this tank.

    The import of `dip_profiler` is deliberately deferred *inside* this function
    to break the circular import cycle:
        tank_calibration → dip_profiler → tank_calibration (_raw_chart_volume)
    """
    try:
        from dip_profiler import get_tilt_coefficient  # noqa: PLC0415
        coeff = get_tilt_coefficient(tank_id, db_path)
        if abs(coeff) < 1e-9:
            return chart_volume                     # no active correction
        corrected = chart_volume + (observed_mm * coeff)
        logger.debug(
            "Tilt correction applied: tank=%s mm=%.1f chart=%.3fL "
            "coeff=%+.6f corrected=%.3fL",
            tank_id, observed_mm, chart_volume, coeff, corrected,
        )
        return max(0.0, corrected)                  # volume cannot be negative
    except Exception as exc:
        # Never raise from a correction lookup — degrade gracefully
        logger.debug("Tilt correction lookup skipped for tank '%s': %s", tank_id, exc)
        return chart_volume


def init_calibration_db(db_path: str = DB_PATH):
    """
    Initializes the tank_calibration_charts table in the SQLite database.
    """
    logger.info(f"Initializing tank_calibration_charts table in database: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tank_calibration_charts (
            tank_id TEXT,
            dip_level_mm INTEGER,
            volume_liters REAL,
            PRIMARY KEY (tank_id, dip_level_mm)
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'tank_calibration_charts' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'tank_calibration_charts' table: {e}")
        raise e


def convert_dip_to_liters(tank_id: str, observed_mm: float, db_path: str = DB_PATH) -> float:
    """
    Converts a raw millimeter dip reading for a given tank to liters using:
      1. Exact match in the calibration chart.
      2. Linear interpolation between bounding chart lines.
      3. Interpolation from zero for readings below the minimum recorded chart millimeter.
      4. Safe fallback capping at capacity for readings above the maximum recorded millimeter.
      5. Safe backward-compatible fallback (returning the input unchanged) if no chart is loaded.
      6. Structural tilt correction: if an active tilt profile exists for this tank
         (computed by dip_profiler.analyze_calibration_drift), the localized drift offset
         is applied AFTER the chart lookup:

             Corrected Volume = Chart Volume(mm) + (mm × localized_tilt_coefficient)

         The correction is a no-op (returns chart volume unchanged) when:
           • No profile exists for this tank_id.
           • correction_active = 0 in tank_tilt_profiles.
           • anomaly_type != 'tilt' (noise or insufficient data).
    """
    # Defensive checks
    if observed_mm is None or observed_mm <= 0.0:
        return 0.0

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Query all chart points for this tank ordered by dip level ascending
        cursor.execute("""
            SELECT dip_level_mm, volume_liters
            FROM tank_calibration_charts
            WHERE tank_id = ?
            ORDER BY dip_level_mm ASC
        """, (tank_id,))
        points = cursor.fetchall()
        conn.close()

        if not points:
            # Safe Fallback: No calibration chart lines loaded for this tank.
            # Return raw input value as volume directly (backward compatible).
            logger.warning(
                f"No calibration lines discovered for tank '{tank_id}' in database. "
                f"Defaulting raw value {observed_mm} directly as volume."
            )
            return float(observed_mm)

        # 1. Exact Match Check
        for x, y in points:
            if abs(x - observed_mm) < 1e-6:
                return _apply_tilt_correction(tank_id, observed_mm, float(y), db_path)

        x_min, y_min = points[0]
        x_max, y_max = points[-1]

        # 2. Below Minimum Bound
        if observed_mm < x_min:
            if x_min <= 0:
                chart_vol = float(y_min)
            else:
                # Interpolate linearly between (0, 0.0) and (x_min, y_min)
                chart_vol = float(observed_mm * y_min / x_min)
            return _apply_tilt_correction(tank_id, observed_mm, chart_vol, db_path)

        # 3. Above Maximum Bound
        if observed_mm > x_max:
            logger.warning(
                f"Observed reading {observed_mm}mm exceeds maximum chart height {x_max}mm "
                f"for tank '{tank_id}'. Capping volume at maximum capacity {y_max} liters."
            )
            return _apply_tilt_correction(tank_id, observed_mm, float(y_max), db_path)

        # 4. Standard Linear Interpolation
        # Loop through points to find bounding indices
        for i in range(len(points) - 1):
            x0, y0 = points[i]
            x1, y1 = points[i + 1]

            if x0 < observed_mm < x1:
                # Linear mathematical interpolation:
                # y = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
                y = y0 + (observed_mm - x0) * (y1 - y0) / (x1 - x0)
                return _apply_tilt_correction(tank_id, observed_mm, float(y), db_path)

        # Catch-all fallback
        return _apply_tilt_correction(tank_id, observed_mm, float(observed_mm), db_path)

    except Exception as e:
        logger.error(f"Error during dip conversion for tank '{tank_id}': {e}")
        # Default back to observed raw reading on unexpected errors
        return float(observed_mm)


def load_calibration_csv(tank_id: str, csv_path: str, db_path: str = DB_PATH) -> int:
    """
    Reads a certified tank calibration chart CSV file and bulk-imports entries
    into the database in a transaction block.
    
    Accepts CSVs with flexible headers:
      - Dip: Column containing 'dip', 'level', or 'mm' (case-insensitive)
      - Volume: Column containing 'volume', 'liter', or 'vol' (case-insensitive)
      - Falls back to first two columns if headers cannot be determined.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Calibration CSV file not found: {csv_path}")
        
    logger.info(f"Loading calibration CSV for tank '{tank_id}' from: {csv_path}")
    
    entries = []
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        # Read the raw lines to find first data row / detect headers
        reader = csv.reader(f)
        rows = list(reader)
        
        if not rows:
            raise ValueError(f"Calibration CSV is empty: {csv_path}")
            
        header = rows[0]
        dip_col_idx = -1
        vol_col_idx = -1
        
        # Analyze headers case-insensitively
        for idx, col in enumerate(header):
            col_clean = col.lower().strip()
            if any(term in col_clean for term in ['dip', 'level', 'mm']):
                dip_col_idx = idx
            elif any(term in col_clean for term in ['volume', 'liter', 'vol']):
                vol_col_idx = idx
                
        # Fallback to positional columns if headers are not detected or collide
        if dip_col_idx == -1 or vol_col_idx == -1 or dip_col_idx == vol_col_idx:
            logger.info("Headers not clearly detected; falling back to positional columns [0: dip, 1: volume]")
            dip_col_idx = 0
            vol_col_idx = 1
            start_row_idx = 1 if len(header) > 0 and not header[0].replace('.', '').replace('-', '').isdigit() else 0
        else:
            start_row_idx = 1
            
        # Parse data rows
        for row in rows[start_row_idx:]:
            if not row or len(row) <= max(dip_col_idx, vol_col_idx):
                continue
            
            dip_str = row[dip_col_idx].strip()
            vol_str = row[vol_col_idx].strip()
            
            if not dip_str or not vol_str:
                continue
                
            try:
                # Convert raw text to numbers (dip = integer mm, volume = float liters)
                dip_val = int(float(dip_str))
                vol_val = float(vol_str)
                entries.append((tank_id, dip_val, vol_val))
            except ValueError as val_err:
                logger.warning(f"Skipped invalid data row {row}: {val_err}")
                
    if not entries:
        raise ValueError(f"No valid calibration data rows could be parsed from {csv_path}")
        
    # Bulk insert into database inside a single transaction
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tank_calibration_charts (
        tank_id TEXT,
        dip_level_mm INTEGER,
        volume_liters REAL,
        PRIMARY KEY (tank_id, dip_level_mm)
    )
    """)
    
    # Remove existing entries for this specific tank to avoid stale overrides
    cursor.execute("DELETE FROM tank_calibration_charts WHERE tank_id = ?", (tank_id,))
    
    cursor.executemany("""
        INSERT OR REPLACE INTO tank_calibration_charts (tank_id, dip_level_mm, volume_liters)
        VALUES (?, ?, ?)
    """, entries)
    
    conn.commit()
    conn.close()
    
    logger.info(f"Successfully imported {len(entries)} calibration lines for '{tank_id}'.")
    return len(entries)
