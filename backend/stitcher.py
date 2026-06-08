"""
stitcher.py — Automated Double-Page Register Splitting Module
=============================================================
Detects whether a scanned image is a two-page spread (landscape register opened
flat on a scanner), locates the binding spine crease, and returns two cleanly
cropped single-page images saved inside /processed_images/.

Public API
----------
    from stitcher import detect_and_split_double_pages

    results = detect_and_split_double_pages("/path/to/scan.jpg")
    # returns:
    #   ["/path/to/processed_images/scan_left.jpg"]          ← single portrait page
    #   [".../scan_left.jpg", ".../scan_right.jpg"]           ← split double spread
    # Each element is an absolute path ready for the intake queue.

Algorithm
---------
1.  Aspect-Ratio Gate   – Only landscape images (width > height × 1.5) are
                          candidates for splitting. Portrait images pass through.
2.  Spine Detection     – Three complementary strategies are run; the first one
                          that produces a confident result wins:
      A. Vertical Intensity Projection
            Convert to grayscale → compute column-wise mean pixel intensity →
            find the darkest vertical band in the central 40% of the image.
            Dark columns = ink shadow at the binding crease.
      B. Hough Line Transform (fallback)
            Canny edge detection → HoughLinesP → isolate near-vertical segments
            in the central zone → vote for the median x-coordinate.
      C. Geometric Midpoint (last resort)
            Simply split at exact pixel centre (width // 2).
3.  Validity Guard      – If the detected spine x-position lies outside the
                          central 25–75% band, fall back to the geometric midpoint
                          to prevent degenerate tiny slivers.
4.  Matrix Slice        – Crop left = img[:, :split_x] and right = img[:, split_x:]
5.  Output              – Save both halves to /processed_images/ and return paths.
"""

import os
import cv2
import numpy as np
from typing import Optional
from logger import logger  # type: ignore

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR   = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BACKEND_DIR, "processed_images")
os.makedirs(PROCESSED_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Width-to-height ratio above which an image is treated as a double-page spread.
LANDSCAPE_RATIO_THRESHOLD: float = float(
    os.getenv("PUMPAI_LANDSCAPE_RATIO", "1.5")
)

# The spine must be located within this fractional band of image width.
# e.g. 0.25 means the spine must be between 25% and 75% of image width.
SPINE_VALID_MARGIN: float = 0.25

# Output JPEG quality for saved split images.
JPEG_QUALITY: int = 93


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_output_paths(source_path: str) -> tuple[str, str]:
    """Derives deterministic left/right output file paths from the source path."""
    base  = os.path.splitext(os.path.basename(source_path))[0]
    left  = os.path.join(PROCESSED_DIR, f"{base}_left.jpg")
    right = os.path.join(PROCESSED_DIR, f"{base}_right.jpg")
    return left, right


def _save(img: np.ndarray, path: str) -> None:
    """Saves an OpenCV BGR matrix to disk as JPEG."""
    ok = cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise IOError(f"stitcher: cv2.imwrite failed for path: {path}")
    logger.info(f"stitcher: saved split page → {path}")


# ---------------------------------------------------------------------------
# Strategy A — Vertical Intensity Projection
# ---------------------------------------------------------------------------

def _spine_via_intensity(gray: np.ndarray, col_start: int, col_end: int) -> Optional[int]:
    """
    Computes per-column mean intensity within the central search zone.
    Returns the x-coordinate of the darkest continuous valley (spine shadow).

    Args:
        gray:      Grayscale image matrix.
        col_start: Left boundary of the central search zone (pixels).
        col_end:   Right boundary of the central search zone (pixels).

    Returns:
        int x-coordinate of the detected spine, or None on failure.
    """
    h, w = gray.shape
    # Crop the central strip for analysis
    strip = gray[:, col_start:col_end].astype(np.float32)

    # Column-wise mean intensity (lower = darker = more shadow)
    col_mean = strip.mean(axis=0)

    # Smooth to suppress noise without blurring the valley
    kernel_size = max(3, (col_end - col_start) // 40)
    if kernel_size % 2 == 0:
        kernel_size += 1
    col_mean_smooth = cv2.GaussianBlur(
        col_mean.reshape(1, -1).astype(np.float32),
        (kernel_size, 1), 0
    ).flatten()

    # Find the darkest column (minimum intensity = deepest shadow)
    local_min_idx = int(np.argmin(col_mean_smooth))
    spine_x = col_start + local_min_idx

    # Confidence check: the valley must be meaningfully darker than surroundings
    valley_val  = col_mean_smooth[local_min_idx]
    overall_avg = col_mean_smooth.mean()
    if valley_val >= overall_avg * 0.92:          # less than 8% darker — not a spine
        logger.debug(
            f"stitcher [intensity]: valley not dark enough "
            f"(val={valley_val:.1f}, avg={overall_avg:.1f}) — strategy inconclusive."
        )
        return None

    logger.debug(f"stitcher [intensity]: spine detected at x={spine_x} "
                 f"(darkness delta={overall_avg - valley_val:.1f})")
    return spine_x


# ---------------------------------------------------------------------------
# Strategy B — Hough Line Transform
# ---------------------------------------------------------------------------

def _spine_via_hough(gray: np.ndarray, col_start: int, col_end: int) -> Optional[int]:
    """
    Detects near-vertical line segments in the central strip via HoughLinesP.
    Aggregates x-votes from segments that span at least 30% of image height.

    Returns:
        int median x-coordinate of qualifying segments, or None on failure.
    """
    h, w = gray.shape
    strip_gray  = gray[:, col_start:col_end]
    strip_edges = cv2.Canny(
        cv2.GaussianBlur(strip_gray, (5, 5), 0),
        threshold1=30, threshold2=90
    )

    min_segment_len = max(50, int(h * 0.30))   # must span ≥ 30% of image height
    lines = cv2.HoughLinesP(
        strip_edges,
        rho=1,
        theta=np.pi / 180,
        threshold=40,
        minLineLength=min_segment_len,
        maxLineGap=20,
    )

    if lines is None or len(lines) == 0:
        logger.debug("stitcher [hough]: no line segments found in central zone.")
        return None

    # Keep only near-vertical segments (angle within ±15° of vertical)
    x_votes = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dy == 0:
            continue
        angle_from_vertical = np.degrees(np.arctan(dx / dy))
        if angle_from_vertical <= 15:
            # Average x-coordinate of this segment, mapped back to full image coords
            x_votes.append(col_start + (x1 + x2) // 2)

    if len(x_votes) < 2:
        logger.debug(f"stitcher [hough]: only {len(x_votes)} qualifying segment(s) — inconclusive.")
        return None

    median_x = int(np.median(x_votes))
    logger.debug(f"stitcher [hough]: spine detected at x={median_x} "
                 f"from {len(x_votes)} vertical segments.")
    return median_x


# ---------------------------------------------------------------------------
# Strategy C — Geometric Midpoint (last resort)
# ---------------------------------------------------------------------------

def _spine_geometric_midpoint(w: int) -> int:
    """Returns the exact pixel centre of the image width."""
    logger.debug(f"stitcher [midpoint]: using geometric centre x={w // 2}.")
    return w // 2


# ---------------------------------------------------------------------------
# Spine detection dispatcher
# ---------------------------------------------------------------------------

def _detect_spine_x(img: np.ndarray) -> tuple[int, str]:
    """
    Runs the three spine detection strategies in order of precision.
    Falls back to the next strategy if the current one is inconclusive.

    Returns:
        (spine_x: int, method_used: str)
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # Central search zone: middle 40% of image width
    col_start = int(w * 0.30)
    col_end   = int(w * 0.70)
    valid_lo  = int(w * SPINE_VALID_MARGIN)
    valid_hi  = int(w * (1.0 - SPINE_VALID_MARGIN))

    # --- Strategy A: Intensity Projection ---
    spine_x = _spine_via_intensity(gray, col_start, col_end)
    method  = "intensity_projection"

    # --- Strategy B: Hough Lines ---
    if spine_x is None:
        spine_x = _spine_via_hough(gray, col_start, col_end)
        method  = "hough_transform"

    # --- Strategy C: Geometric Midpoint ---
    if spine_x is None:
        spine_x = _spine_geometric_midpoint(w)
        method  = "geometric_midpoint"

    # Validity guard — prevent degenerate slivers
    if not (valid_lo <= spine_x <= valid_hi):
        logger.warning(
            f"stitcher: detected spine x={spine_x} falls outside valid band "
            f"[{valid_lo}, {valid_hi}]. Falling back to geometric midpoint."
        )
        spine_x = w // 2
        method  = "geometric_midpoint_fallback"

    logger.info(f"stitcher: spine located at x={spine_x}/{w} via [{method}].")
    return spine_x, method


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_and_split_double_pages(
    image_path: str,
    *,
    force_split: bool = False,
    landscape_ratio: float = LANDSCAPE_RATIO_THRESHOLD,
) -> list[str]:
    """
    Evaluates an image file for a double-page register layout and splits it
    if necessary.

    Args:
        image_path:      Absolute path to the source image file.
        force_split:     If True, skip the aspect-ratio check and always split.
        landscape_ratio: Width/height ratio above which splitting is triggered
                         (default: 1.5 × height).

    Returns:
        list[str]: List of absolute paths to output files.
          - Single-page portrait → [image_path]  (returned as-is, no copy)
          - Double-page spread   → [left_path, right_path]

    Raises:
        FileNotFoundError: If image_path does not exist.
        IOError:           If OpenCV cannot decode the image.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"stitcher: image not found at '{image_path}'.")

    # Load full-colour image
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f"stitcher: OpenCV failed to decode image at '{image_path}'.")

    h, w = img.shape[:2]
    ratio = w / h if h > 0 else 0.0
    logger.info(
        f"stitcher: processing '{os.path.basename(image_path)}' "
        f"({w}×{h}px, ratio={ratio:.2f})"
    )

    # ── 1. Aspect-Ratio Gate ─────────────────────────────────────────────
    if not force_split and ratio < landscape_ratio:
        logger.info(
            f"stitcher: aspect ratio {ratio:.2f} < threshold {landscape_ratio} "
            "→ portrait sheet, no split required."
        )
        return [image_path]

    logger.info(
        f"stitcher: landscape spread detected (ratio={ratio:.2f} ≥ {landscape_ratio}). "
        "Initiating spine detection and split."
    )

    # ── 2. Spine Detection ───────────────────────────────────────────────
    spine_x, method = _detect_spine_x(img)

    # ── 3. Matrix Slice ──────────────────────────────────────────────────
    left_matrix  = img[:, :spine_x]
    right_matrix = img[:, spine_x:]

    # Guard against zero-width slices (can happen with extreme spine positions)
    if left_matrix.shape[1] == 0 or right_matrix.shape[1] == 0:
        logger.warning(
            f"stitcher: degenerate split at x={spine_x} — returning original image."
        )
        return [image_path]

    # ── 4. Save Outputs ──────────────────────────────────────────────────
    left_path, right_path = _build_output_paths(image_path)
    _save(left_matrix,  left_path)
    _save(right_matrix, right_path)

    # Release large matrices
    del left_matrix, right_matrix, img

    logger.info(
        f"stitcher: split complete via [{method}] at x={spine_x}. "
        f"Left={os.path.basename(left_path)}, Right={os.path.basename(right_path)}"
    )
    return [left_path, right_path]


# ---------------------------------------------------------------------------
# Batch helper (convenience wrapper for the bulk importer)
# ---------------------------------------------------------------------------

def split_image_list(image_paths: list[str], **kwargs) -> list[str]:
    """
    Runs detect_and_split_double_pages() on a list of image paths.
    Flattens the results into a single ordered list.

    Portrait pages pass through unchanged; landscape spreads are replaced
    with their two halves.

    Args:
        image_paths: List of absolute image paths.
        **kwargs:    Forwarded to detect_and_split_double_pages().

    Returns:
        list[str]: Flat list of all output paths.
    """
    output: list[str] = []
    for path in image_paths:
        try:
            pages = detect_and_split_double_pages(path, **kwargs)
            output.extend(pages)
        except Exception as e:
            logger.error(f"stitcher.split_image_list: error processing '{path}' — {e}")
            output.append(path)   # keep original in queue rather than silently dropping
    return output
