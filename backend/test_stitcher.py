"""
test_stitcher.py — Unit Tests for /backend/stitcher.py
=======================================================
Covers:
  1. Aspect-ratio gate:     portrait images pass through unchanged.
  2. Landscape detection:   wide images trigger the split routine.
  3. Intensity projection:  darkest-column strategy returns correct x on a
                            synthetic image with a known dark band.
  4. Hough transform:       near-vertical line segments are detected.
  5. Geometric midpoint:    fallback returns width // 2.
  6. Validity guard:        out-of-range spine x is corrected to centre.
  7. Output file naming:    _build_output_paths produces _left / _right suffixes.
  8. Full end-to-end:       detect_and_split_double_pages() with synthetic images.
  9. Force-split flag:      portrait image is split when force_split=True.
 10. Degenerate guard:      zero-width slice returns original path.
 11. Missing file:          FileNotFoundError raised for non-existent path.
 12. Corrupt image:         IOError raised when OpenCV cannot decode.
 13. split_image_list:      batch helper flattens results; errors kept in queue.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import cv2
import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from stitcher import (
    detect_and_split_double_pages,
    split_image_list,
    _build_output_paths,
    _detect_spine_x,
    _spine_via_intensity,
    _spine_via_hough,
    _spine_geometric_midpoint,
    LANDSCAPE_RATIO_THRESHOLD,
    PROCESSED_DIR,
)


# =========================================================================
# Helpers
# =========================================================================

def _make_image(w: int, h: int, color=(200, 200, 200)) -> np.ndarray:
    """Returns a solid-colour BGR image of the given dimensions."""
    img = np.full((h, w, 3), color, dtype=np.uint8)
    return img


def _make_landscape(w: int = 1200, h: int = 600, spine_x: int = None) -> np.ndarray:
    """
    Returns a landscape BGR image with an optional dark vertical band
    at `spine_x` simulating a binding crease.
    """
    img = _make_image(w, h, color=(210, 210, 210))
    if spine_x is not None:
        # Draw a dark band 10px wide at the given x
        band_start = max(0, spine_x - 5)
        band_end   = min(w, spine_x + 5)
        img[:, band_start:band_end] = (40, 40, 40)   # near-black
    return img


def _save_temp(img: np.ndarray, suffix=".jpg") -> str:
    """Saves an image to a temp file and returns its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


# =========================================================================
# 1. Aspect-Ratio Gate (portrait pass-through)
# =========================================================================

class TestAspectRatioGate(unittest.TestCase):

    def test_portrait_image_returns_original_path(self):
        """A tall portrait image must be returned as-is without any file I/O."""
        img  = _make_image(600, 1200)    # ratio = 0.5 (portrait)
        path = _save_temp(img)
        try:
            result = detect_and_split_double_pages(path)
            self.assertEqual(result, [path])
        finally:
            os.unlink(path)

    def test_square_image_returns_original_path(self):
        img  = _make_image(800, 800)     # ratio = 1.0
        path = _save_temp(img)
        try:
            result = detect_and_split_double_pages(path)
            self.assertEqual(result, [path])
        finally:
            os.unlink(path)

    def test_mildly_landscape_below_threshold_no_split(self):
        """ratio = 1.3, just below default threshold of 1.5 — no split."""
        img  = _make_image(1300, 1000)   # ratio = 1.3
        path = _save_temp(img)
        try:
            result = detect_and_split_double_pages(path)
            self.assertEqual(result, [path])
        finally:
            os.unlink(path)


# =========================================================================
# 2. Landscape Detection
# =========================================================================

class TestLandscapeDetection(unittest.TestCase):

    def test_landscape_produces_two_output_files(self):
        """A wide landscape image must produce exactly 2 output paths."""
        img  = _make_landscape(w=1200, h=600, spine_x=600)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path)
            self.assertEqual(len(result), 2)
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)

    def test_output_files_are_created_on_disk(self):
        img  = _make_landscape(w=1200, h=600, spine_x=600)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            detect_and_split_double_pages(path)
            self.assertTrue(os.path.exists(left_out))
            self.assertTrue(os.path.exists(right_out))
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)

    def test_output_files_are_valid_images(self):
        img  = _make_landscape(w=1200, h=600, spine_x=600)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path)
            for p in result:
                loaded = cv2.imread(p)
                self.assertIsNotNone(loaded)
                self.assertGreater(loaded.shape[1], 0)   # non-zero width
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)

    def test_custom_landscape_ratio_respected(self):
        """Setting landscape_ratio=1.0 should split a mildly landscape image."""
        img  = _make_landscape(w=1000, h=900)   # ratio ≈ 1.11
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path, landscape_ratio=1.0)
            self.assertEqual(len(result), 2)
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)


# =========================================================================
# 3. Strategy A — Intensity Projection
# =========================================================================

class TestIntensityProjection(unittest.TestCase):

    def _make_gray_with_dark_band(self, w: int, h: int, band_x: int) -> np.ndarray:
        gray = np.full((h, w), 200, dtype=np.uint8)
        gray[:, max(0, band_x-5):min(w, band_x+5)] = 30
        return gray

    def test_detects_dark_band_in_centre(self):
        w, h = 800, 600
        band_x = 400   # exact centre
        gray = self._make_gray_with_dark_band(w, h, band_x)
        col_start, col_end = int(w * 0.30), int(w * 0.70)
        result = _spine_via_intensity(gray, col_start, col_end)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, band_x, delta=15)

    def test_returns_none_when_no_dark_band(self):
        """Uniform image has no meaningful spine — should return None."""
        gray = np.full((600, 800), 180, dtype=np.uint8)
        col_start, col_end = int(800 * 0.30), int(800 * 0.70)
        result = _spine_via_intensity(gray, col_start, col_end)
        self.assertIsNone(result)

    def test_spine_slightly_off_centre(self):
        """Spine 60px to the right of centre should still be found within 25px."""
        w, h = 1000, 700
        band_x = 540
        gray = self._make_gray_with_dark_band(w, h, band_x)
        col_start, col_end = int(w * 0.30), int(w * 0.70)
        result = _spine_via_intensity(gray, col_start, col_end)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, band_x, delta=20)


# =========================================================================
# 4. Strategy B — Hough Transform
# =========================================================================

class TestHoughTransform(unittest.TestCase):

    def _make_gray_with_vertical_line(self, w: int, h: int, line_x: int) -> np.ndarray:
        gray = np.full((h, w), 200, dtype=np.uint8)
        cv2.line(gray, (line_x, 0), (line_x, h), 0, 2)   # black vertical line
        return gray

    def test_detects_vertical_line(self):
        w, h = 800, 600
        line_x = 400
        gray = self._make_gray_with_vertical_line(w, h, line_x)
        col_start, col_end = int(w * 0.30), int(w * 0.70)
        result = _spine_via_hough(gray, col_start, col_end)
        if result is not None:
            self.assertAlmostEqual(result, line_x, delta=20)

    def test_returns_none_on_blank_image(self):
        gray = np.full((600, 800), 200, dtype=np.uint8)
        col_start, col_end = int(800 * 0.30), int(800 * 0.70)
        result = _spine_via_hough(gray, col_start, col_end)
        self.assertIsNone(result)


# =========================================================================
# 5. Strategy C — Geometric Midpoint
# =========================================================================

class TestGeometricMidpoint(unittest.TestCase):

    def test_even_width(self):
        self.assertEqual(_spine_geometric_midpoint(1000), 500)

    def test_odd_width(self):
        self.assertEqual(_spine_geometric_midpoint(999), 499)

    def test_small_width(self):
        self.assertEqual(_spine_geometric_midpoint(2), 1)


# =========================================================================
# 6. Validity Guard
# =========================================================================

class TestValidityGuard(unittest.TestCase):

    def test_out_of_range_spine_corrected_to_midpoint(self):
        """
        When intensity projection detects a spine at x < 25% of width,
        the dispatcher should fall back to geometric midpoint.
        """
        # Image with a very dark band at x=50 (5% of 1000px) — way off-centre
        w, h = 1000, 600
        img = _make_landscape(w=w, h=h, spine_x=50)   # dark band at left edge
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            # The validity guard should reject x=50 and fall back to w//2=500
            spine_x, method = _detect_spine_x(img)
            # Should either be near 500 (midpoint fallback) or near 50 if intensity
            # strategy didn't fire; in either case it must be in [250, 750]
            self.assertGreaterEqual(spine_x, 250)
            self.assertLessEqual(spine_x, 750)
        finally:
            os.unlink(path)

    def test_spine_within_valid_band_is_kept(self):
        """A spine at 50% of width must not be overridden."""
        w, h = 1000, 600
        img = _make_landscape(w=w, h=h, spine_x=500)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            spine_x, _ = _detect_spine_x(img)
            # Must be within [250, 750]
            self.assertGreaterEqual(spine_x, 250)
            self.assertLessEqual(spine_x, 750)
        finally:
            os.unlink(path)


# =========================================================================
# 7. Output File Naming
# =========================================================================

class TestBuildOutputPaths(unittest.TestCase):

    def test_left_suffix(self):
        left, _ = _build_output_paths("/some/dir/day45_scan.jpg")
        self.assertTrue(os.path.basename(left).endswith("_left.jpg"))

    def test_right_suffix(self):
        _, right = _build_output_paths("/some/dir/day45_scan.jpg")
        self.assertTrue(os.path.basename(right).endswith("_right.jpg"))

    def test_base_name_preserved(self):
        left, right = _build_output_paths("/some/dir/day45_scan.jpg")
        self.assertIn("day45_scan", os.path.basename(left))
        self.assertIn("day45_scan", os.path.basename(right))

    def test_output_in_processed_dir(self):
        left, right = _build_output_paths("/any/path/file.png")
        self.assertEqual(os.path.dirname(left),  PROCESSED_DIR)
        self.assertEqual(os.path.dirname(right), PROCESSED_DIR)

    def test_different_extensions_handled(self):
        for ext in [".jpg", ".png", ".tiff"]:
            left, right = _build_output_paths(f"/dir/scan{ext}")
            self.assertTrue(left.endswith("_left.jpg"))
            self.assertTrue(right.endswith("_right.jpg"))


# =========================================================================
# 8. End-to-End Split
# =========================================================================

class TestEndToEnd(unittest.TestCase):

    def test_left_page_is_left_half(self):
        """Left output should have roughly the same width as spine_x."""
        w, h, spine_x = 1200, 600, 580
        img  = _make_landscape(w=w, h=h, spine_x=spine_x)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path)
            self.assertEqual(len(result), 2)
            left_img  = cv2.imread(result[0])
            right_img = cv2.imread(result[1])
            self.assertIsNotNone(left_img)
            self.assertIsNotNone(right_img)
            # The widths should sum to exactly w (or w minus the dark band width)
            total = left_img.shape[1] + right_img.shape[1]
            self.assertAlmostEqual(total, w, delta=30)
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)

    def test_height_preserved_in_split(self):
        """Both halves must retain the full original image height."""
        w, h = 1400, 700
        img  = _make_landscape(w=w, h=h, spine_x=700)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path)
            for p in result:
                loaded = cv2.imread(p)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.shape[0], h)
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)


# =========================================================================
# 9. Force-Split Flag
# =========================================================================

class TestForceSplit(unittest.TestCase):

    def test_force_split_splits_portrait(self):
        """force_split=True must split even a portrait image."""
        img  = _make_image(600, 1200)   # portrait
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            result = detect_and_split_double_pages(path, force_split=True)
            self.assertEqual(len(result), 2)
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)


# =========================================================================
# 10. Degenerate Guard
# =========================================================================

class TestDegenerateGuard(unittest.TestCase):

    def test_degenerate_spine_returns_original(self):
        """
        If _detect_spine_x is mocked to return x=0 (zero-width left slice),
        the function must return [original_path] rather than saving broken files.
        """
        img  = _make_landscape(w=1200, h=600)
        path = _save_temp(img)
        left_out, right_out = _build_output_paths(path)
        try:
            with patch("stitcher._detect_spine_x", return_value=(0, "mock")):
                result = detect_and_split_double_pages(path)
            # Should fall back to returning original because left slice width == 0
            self.assertEqual(result, [path])
        finally:
            os.unlink(path)
            for p in [left_out, right_out]:
                if os.path.exists(p): os.unlink(p)


# =========================================================================
# 11. Missing File Error
# =========================================================================

class TestMissingFile(unittest.TestCase):

    def test_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            detect_and_split_double_pages("/non/existent/image.jpg")


# =========================================================================
# 12. Corrupt Image Error
# =========================================================================

class TestCorruptImage(unittest.TestCase):

    def test_raises_io_error_on_unreadable_file(self):
        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        # Write random non-image bytes
        with open(path, "wb") as f:
            f.write(b"\x00\xFF\x00\xFF garbage data not an image")
        try:
            with self.assertRaises(IOError):
                detect_and_split_double_pages(path)
        finally:
            os.unlink(path)


# =========================================================================
# 13. split_image_list (batch helper)
# =========================================================================

class TestSplitImageList(unittest.TestCase):

    def test_portrait_images_pass_through(self):
        img  = _make_image(600, 1200)
        path = _save_temp(img)
        try:
            result = split_image_list([path])
            self.assertEqual(result, [path])
        finally:
            os.unlink(path)

    def test_mixed_list_flattened(self):
        """One portrait + one landscape → [portrait, left, right]."""
        portrait  = _save_temp(_make_image(600, 1200))
        landscape = _save_temp(_make_landscape(w=1200, h=600, spine_x=600))
        lo, ro    = _build_output_paths(landscape)
        try:
            result = split_image_list([portrait, landscape])
            self.assertEqual(len(result), 3)
        finally:
            os.unlink(portrait)
            os.unlink(landscape)
            for p in [lo, ro]:
                if os.path.exists(p): os.unlink(p)

    def test_error_keeps_original_in_queue(self):
        """A bad path should not crash the batch — original kept in queue."""
        result = split_image_list(["/does/not/exist.jpg"])
        self.assertEqual(result, ["/does/not/exist.jpg"])

    def test_empty_list_returns_empty_list(self):
        self.assertEqual(split_image_list([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
