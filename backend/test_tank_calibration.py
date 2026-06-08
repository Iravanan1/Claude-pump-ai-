"""
Comprehensive unit tests for tank_calibration.py.
"""

import os
import sqlite3
import tempfile
import unittest
import shutil

# Make sure backend can be imported from this directory
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tank_calibration import (
    init_calibration_db,
    convert_dip_to_liters,
    load_calibration_csv
)
from reconciliation import save_reconciliation, get_reconciliation, init_recon_db


class TestTankCalibration(unittest.TestCase):

    def setUp(self):
        # Create a fresh temporary database
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        init_calibration_db(self.db_path)
        init_recon_db(self.db_path)
        
        # Temp dir for writing dummy CSVs
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
            
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_sample_chart(self):
        """Seeds a sample calibration chart directly into the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        points = [
            ('Tank_1_HSD', 100, 500.0),
            ('Tank_1_HSD', 200, 1200.0),
            ('Tank_1_HSD', 300, 2000.0),
            ('Tank_2_MS', 150, 600.0),
            ('Tank_2_MS', 300, 1500.0),
        ]
        cursor.executemany("""
            INSERT INTO tank_calibration_charts (tank_id, dip_level_mm, volume_liters)
            VALUES (?, ?, ?)
        """, points)
        conn.commit()
        conn.close()

    def test_init_calibration_db(self):
        """Verifies table structure initialization is complete."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(tank_calibration_charts)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()
        
        self.assertIn("tank_id", columns)
        self.assertIn("dip_level_mm", columns)
        self.assertIn("volume_liters", columns)

    def test_convert_dip_exact_match(self):
        """Verifies that an exact millimeter reading returns the exact volume directly."""
        self._seed_sample_chart()
        
        vol_hsd = convert_dip_to_liters('Tank_1_HSD', 200.0, db_path=self.db_path)
        vol_ms = convert_dip_to_liters('Tank_2_MS', 150.0, db_path=self.db_path)
        
        self.assertEqual(vol_hsd, 1200.0)
        self.assertEqual(vol_ms, 600.0)

    def test_convert_dip_linear_interpolation(self):
        """Verifies linear mathematical interpolation for intermediate dip readings."""
        self._seed_sample_chart()
        
        # 150mm HSD should sit exactly halfway between 100mm (500L) and 200mm (1200L) -> 850L
        vol_1 = convert_dip_to_liters('Tank_1_HSD', 150.0, db_path=self.db_path)
        self.assertEqual(vol_1, 850.0)
        
        # 250mm HSD sits halfway between 200mm (1200L) and 300mm (2000L) -> 1600L
        vol_2 = convert_dip_to_liters('Tank_1_HSD', 250.0, db_path=self.db_path)
        self.assertEqual(vol_2, 1600.0)
        
        # 225mm MS sits halfway between 150mm (600L) and 300mm (1500L) -> 1050L
        vol_3 = convert_dip_to_liters('Tank_2_MS', 225.0, db_path=self.db_path)
        self.assertEqual(vol_3, 1050.0)

    def test_convert_dip_boundary_and_nulls(self):
        """Verifies out-of-bounds, zero, negative, and extreme readings are handled gracefully."""
        self._seed_sample_chart()
        
        # Below 0 or None should instantly return 0.0
        self.assertEqual(convert_dip_to_liters('Tank_1_HSD', 0.0, db_path=self.db_path), 0.0)
        self.assertEqual(convert_dip_to_liters('Tank_1_HSD', -50.0, db_path=self.db_path), 0.0)
        self.assertEqual(convert_dip_to_liters('Tank_1_HSD', None, db_path=self.db_path), 0.0)
        
        # Below minimum recorded millimeter x_min (e.g. 50mm HSD sits halfway between 0 and 100mm/500L) -> 250L
        vol_low = convert_dip_to_liters('Tank_1_HSD', 50.0, db_path=self.db_path)
        self.assertEqual(vol_low, 250.0)
        
        # Above maximum recorded millimeter x_max (e.g. 400mm HSD exceeds 300mm/2000L) -> Caps at 2000L
        vol_high = convert_dip_to_liters('Tank_1_HSD', 400.0, db_path=self.db_path)
        self.assertEqual(vol_high, 2000.0)

    def test_convert_dip_backward_fallback(self):
        """Verifies that empty databases/unseeded tanks fallback to returning inputs unchanged."""
        # Querying a random tank name with no database calibration entries
        vol = convert_dip_to_liters('Tank_Unseeded_HSD', 1450.5, db_path=self.db_path)
        self.assertEqual(vol, 1450.5)

    def test_load_calibration_csv_standard_headers(self):
        """Tests importing certified charts from standard headers CSV."""
        csv_content = (
            "dip_level_mm,volume_liters\n"
            "100,450.0\n"
            "200,1050.5\n"
            "300,1850.0\n"
        )
        csv_file = os.path.join(self.temp_dir, "chart_standard.csv")
        with open(csv_file, 'w', encoding='utf-8') as f:
            f.write(csv_content)
            
        rows_imported = load_calibration_csv('Tank_3_HSD', csv_file, db_path=self.db_path)
        self.assertEqual(rows_imported, 3)
        
        # Verify lookups work perfectly on loaded chart
        self.assertEqual(convert_dip_to_liters('Tank_3_HSD', 100.0, db_path=self.db_path), 450.0)
        self.assertEqual(convert_dip_to_liters('Tank_3_HSD', 150.0, db_path=self.db_path), 750.25)
        self.assertEqual(convert_dip_to_liters('Tank_3_HSD', 350.0, db_path=self.db_path), 1850.0)

    def test_load_calibration_csv_flexible_headers_and_spaces(self):
        """Tests importing CSV sheets with flexible header variations and spaces."""
        csv_content = (
            "  Dip Level (mm)  ,   Volume (Ltr)  \n"
            "50, 200.0\n"
            "150, 800.0\n"
        )
        csv_file = os.path.join(self.temp_dir, "chart_flexible.csv")
        with open(csv_file, 'w', encoding='utf-8') as f:
            f.write(csv_content)
            
        rows_imported = load_calibration_csv('Tank_4_MS', csv_file, db_path=self.db_path)
        self.assertEqual(rows_imported, 2)
        self.assertEqual(convert_dip_to_liters('Tank_4_MS', 100.0, db_path=self.db_path), 500.0)

    def test_load_calibration_csv_positional_fallback(self):
        """Tests CSV import positional column fallbacks if headers are absent."""
        csv_content = (
            "80, 320.0\n"
            "180, 920.0\n"
        )
        csv_file = os.path.join(self.temp_dir, "chart_no_header.csv")
        with open(csv_file, 'w', encoding='utf-8') as f:
            f.write(csv_content)
            
        rows_imported = load_calibration_csv('Tank_5_HSD', csv_file, db_path=self.db_path)
        self.assertEqual(rows_imported, 2)
        self.assertEqual(convert_dip_to_liters('Tank_5_HSD', 130.0, db_path=self.db_path), 620.0)

    def test_save_reconciliation_integration_with_dips(self):
        """Verifies save_reconciliation automatically converts millimeters to liters."""
        self._seed_sample_chart()
        
        # Save a reconciliation day passing rod millimeter levels (e.g. 150mm HSD and 225mm MS)
        save_reconciliation(
            date_str="2026-06-01",
            hsd_opening=100.0,  # 100mm -> 500.0L
            hsd_receipt=2000.0,
            hsd_closing=200.0,  # 200mm -> 1200.0L
            ms_opening=150.0,   # 150mm -> 600.0L
            ms_receipt=3000.0,
            ms_closing=300.0,   # 300mm -> 1500.0L
            actual_cash=100000.0,
            digital_settlements=50000.0,
            udhaar_entries=20000.0,
            db_path=self.db_path
        )
        
        # Retrieve reconciliation entry from DB
        recon = get_reconciliation("2026-06-01", db_path=self.db_path)
        
        # Verify values stored in stock_recon are fully converted liter volumes
        self.assertEqual(recon["hsd_opening_dip_liters"], 500.0)
        self.assertEqual(recon["hsd_closing_dip_liters"], 1200.0)
        self.assertEqual(recon["ms_opening_dip_liters"], 600.0)
        self.assertEqual(recon["ms_closing_dip_liters"], 1500.0)


if __name__ == "__main__":
    unittest.main()
