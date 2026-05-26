"""
Detects the left and right edges of the metal sheet and measures whether
the hole pattern is centered between them.

When a ROI is provided (the common case), edges are detected using narrow
search windows around the ROI boundaries in the FULL-FRAME image, where the
backlights are visible. A gradient-based approach (bright->dark for the left
edge, dark->bright for the right) locates the actual metal-to-backlight
transition per horizontal band.

When no ROI is given, falls back to column-profile threshold detection on
whatever image is passed (legacy path, works when backlight is in frame).

All coordinates returned in CenteringResult are in ROI space (same space as
hole coordinates), so the drawing overlay works without extra transforms.

Edge detection strategy (with ROI):
  - Full frame divided into N_BANDS horizontal bands (only within ROI y-extent).
  - Per band, a narrow search window is taken from the full-frame R channel.
  - Rows downsampled DS for speed; column mean profile -> smoothed -> gradient.
  - Left: sharpest bright->dark drop; Right: sharpest dark->bright rise.
  - Per-band edge X is converted to ROI-relative before storing.
  - A robust line (sigma-clip polyfit) is fitted to per-band edge points.
  - The scalar left_x / right_x are evaluated at mid-height from that line.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_N_BANDS = 16
_MIN_RELIABLE_BANDS = 6

# Search window half-widths (px, full-frame coords) around ROI edges.
_LEFT_INNER  =  80   # how far inside  the ROI left  edge to search
_LEFT_OUTER  = 350   # how far outside the ROI left  edge to search
_RIGHT_INNER =  80   # how far inside  the ROI right edge to search
_RIGHT_OUTER = 350   # how far outside the ROI right edge to search

# Row downsampling factor for speed (4 = 4x fewer rows to average per band).
_DS = 4

# Smoothing kernel for column profile (small to preserve gradient magnitude).
_SMOOTH_K = 7


@dataclass(frozen=True)
class CenteringResult:
    left_x: float           # left metal edge X  (px, ROI coords) -- fitted line at mid-height
    right_x: float          # right metal edge X (px, ROI coords)
    sheet_center_x: float   # midpoint between sheet edges
    holes_center_x: float   # mean X of detected holes
    offset_px: float        # margin_delta_px / 2  (+ = pattern shifted right)
    sheet_width_px: float   # right_x - left_x
    within_tol: bool        # |offset_px| <= tol_px  (always True when tol_px == 0)
    pattern_left_x: float   # min(hole.x - hole.r) -- left physical bound of detected pattern
    pattern_right_x: float  # max(hole.x + hole.r) -- right physical bound of detected pattern
    left_margin_px: float   # pattern_left_x - sheet_left_x
    right_margin_px: float  # sheet_right_x - pattern_right_x
    margin_delta_px: float  # left_margin_px - right_margin_px (>0 = pattern shifted right)

    # Per-band real measurement points in ROI coords (tuple of (x, y) sorted by y)
    left_edge_points: tuple = field(default_factory=tuple)
    right_edge_points: tuple = field(default_factory=tuple)
    pattern_left_points: tuple = field(default_factory=tuple)
    pattern_right_points: tuple = field(default_factory=tuple)

    # Per-band margin variation (std dev across bands, 0 if < 2 matched bands)
    left_margin_std: float = 0.0
    right_margin_std: float = 0.0

    # False if too few bands were detected to trust the measurement
    centering_reliable: bool = True

    # Slope of each edge line from vertical (degrees). 0° = perfectly vertical.
    # Positive = edge leans right as Y increases. Measured from per-band points.
    left_edge_slope_deg: float = 0.0
    right_edge_slope_deg: float = 0.0
    pattern_left_slope_deg: float = 0.0
    pattern_right_slope_deg: float = 0.0

    # Horizontal residuals of per-band SHEET EDGE points from a robust fitted line.
    # High values indicate camera/sheet vibration (frame image quality issue).
    # 0.0 when insufficient points (<3) for a line fit.
    chapa_zigzag_std_px: float = 0.0
    chapa_zigzag_max_px: float = 0.0

    # Horizontal residuals of per-band PATTERN (hole) edge points from a robust fitted line.
    # High values indicate pattern misalignment within the sheet (mechanical issue).
    # 0.0 when insufficient points (<3) for a line fit.
    pattern_zigzag_std_px: float = 0.0
    pattern_zigzag_max_px: float = 0.0

    # Horizontal residuals of per-band PATTERN CENTER (median X of all holes per band).
    # Catches internal zigzag that the outer-border metric misses.
    # 0.0 when insufficient points (<3) for a line fit.
    pattern_center_zigzag_std_px: float = 0.0
    pattern_center_zigzag_max_px: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_sheet_edges_in_windows(
    img_full: np.ndarray,
    roi_x: int,
    roi_w: int,
    roi_y: int,
    roi_h: int,
    n_bands: int = _N_BANDS,
    ds: int = _DS,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Per-band left/right metal edge detection in search windows.

    Works on the full-frame R channel (backlight is red/bright).
    Returns two dicts keyed by band index with values (x_roi_relative, y_roi_relative).
    Bands where no clear bright/dark transition is found are omitted.

    Left window covers [roi_x - _LEFT_OUTER, roi_x + _LEFT_INNER] in full-frame x.
    Right window covers [roi_x + roi_w - _RIGHT_INNER, roi_x + roi_w + _RIGHT_OUTER].
    The gradient peak (bright->dark for left, dark->bright for right) locates the edge.
    """
    H, W = img_full.shape[:2]
    r_ch = img_full[:, :, 2].astype(np.float32)   # R channel

    lw_x0 = max(0, roi_x - _LEFT_OUTER)
    lw_x1 = min(W - 1, roi_x + _LEFT_INNER)
    rw_x0 = max(0, roi_x + roi_w - _RIGHT_INNER)
    rw_x1 = min(W - 1, roi_x + roi_w + _RIGHT_OUTER)

    band_h = roi_h / n_bands
    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0_full = roi_y + int(i * band_h)
        y1_full = roi_y + min(int((i + 1) * band_h), roi_h)
        if y1_full - y0_full < 4:
            continue

        cy_roi = float((y0_full - roi_y) + (y1_full - roi_y)) / 2.0

        # Left edge: find sharpest bright->dark drop
        if lw_x1 > lw_x0 + 4:
            strip = r_ch[y0_full:y1_full:ds, lw_x0:lw_x1]
            if strip.size == 0:
                continue
            col_prof = strip.mean(axis=0)
            # Small kernel: rows are already averaged so noise is low.
            # A large kernel would spread the edge and kill the gradient.
            if len(col_prof) > _SMOOTH_K:
                col_s = cv2.GaussianBlur(
                    col_prof.reshape(1, -1), (_SMOOTH_K, 1), 0
                ).ravel()
            else:
                col_s = col_prof
            rng = float(col_s.max() - col_s.min())
            if rng < 20.0:
                continue  # no meaningful bright/dark contrast in this band
            grad = np.diff(col_s)
            if grad.size == 0:
                continue
            idx = int(np.argmin(grad))   # index of steepest drop
            # Accept: gradient must be negative AND at least 3% of full range.
            # (3% is very permissive; rng >= 20 is the real quality gate.)
            if grad[idx] < -rng * 0.03:
                left_dict[i] = (float(lw_x0 + idx) - roi_x, cy_roi)

        # Right edge: find sharpest dark->bright rise
        if rw_x1 > rw_x0 + 4:
            strip = r_ch[y0_full:y1_full:ds, rw_x0:rw_x1]
            if strip.size == 0:
                continue
            col_prof = strip.mean(axis=0)
            if len(col_prof) > _SMOOTH_K:
                col_s = cv2.GaussianBlur(
                    col_prof.reshape(1, -1), (_SMOOTH_K, 1), 0
                ).ravel()
            else:
                col_s = col_prof
            rng = float(col_s.max() - col_s.min())
            if rng < 20.0:
                continue
            grad = np.diff(col_s)
            if grad.size == 0:
                continue
            idx = int(np.argmax(grad))   # index of steepest rise
            if grad[idx] > rng * 0.03:
                right_dict[i] = (float(rw_x0 + idx) - roi_x, cy_roi)

    return left_dict, right_dict


def _background_threshold(gray: np.ndarray, w: int) -> float:
    """Estimate metal-vs-background threshold from full-image column profile."""
    col_full = np.percentile(gray, 20, axis=0).astype(np.float32)
    k = max(5, w // 30)
    k = k + 1 if k % 2 == 0 else k
    col_smooth = cv2.GaussianBlur(col_full.reshape(1, -1), (k, 1), 0).ravel()
    bg_level = float(np.percentile(col_smooth, 95))
    return bg_level * 0.70


def _detect_edges_by_band(
    img_bgr: np.ndarray,
    n_bands: int = _N_BANDS,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Legacy band-based edge detection (threshold on column profile).

    Used only when no ROI is provided (roi=None path).
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    metal_thresh = _background_threshold(gray, w)
    band_h = h / n_bands
    min_metal_cols = max(5, int(w * 0.05))

    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0 = int(i * band_h)
        y1 = min(int((i + 1) * band_h), h)
        if y1 - y0 < 8:
            continue

        band = gray[y0:y1, :]
        col_pct = np.percentile(band, 20, axis=0).astype(np.float32)

        k_b = max(3, w // 60)
        k_b = k_b + 1 if k_b % 2 == 0 else k_b
        col_b = cv2.GaussianBlur(col_pct.reshape(1, -1), (k_b, 1), 0).ravel()

        metal_cols = np.where(col_b < metal_thresh)[0]
        if len(metal_cols) < min_metal_cols:
            continue

        cy = (y0 + y1) / 2.0
        left_dict[i]  = (float(metal_cols[0]),  cy)
        right_dict[i] = (float(metal_cols[-1]), cy)

    return left_dict, right_dict


def _pattern_bounds_by_band(
    holes: Sequence,
    img_h: int,
    n_bands: int = _N_BANDS,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Per-band left/right physical bounds using detected hole positions."""
    band_h = img_h / n_bands
    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0 = i * band_h
        y1 = (i + 1) * band_h
        band_holes = [hh for hh in holes if y0 <= hh.y < y1]
        if not band_holes:
            continue
        cy = (y0 + y1) / 2.0
        left_dict[i]  = (float(min(hh.x - hh.r for hh in band_holes)), cy)
        right_dict[i] = (float(max(hh.x + hh.r for hh in band_holes)), cy)

    return left_dict, right_dict


def _pattern_center_by_band(
    left_by_band: dict[int, tuple[float, float]],
    right_by_band: dict[int, tuple[float, float]],
) -> list[tuple[float, float]]:
    """Per-band center between physical pattern bounds.

    Median X of all holes is too sensitive for staggered microperforated grids:
    alternating rows can move the median even when the physical pattern is fine.
    The center between left/right pattern bounds is steadier and still catches
    real lateral waviness.
    """
    points: list[tuple[float, float]] = []
    for i in sorted(set(left_by_band) & set(right_by_band)):
        lx, ly = left_by_band[i]
        rx, ry = right_by_band[i]
        points.append(((lx + rx) / 2.0, (ly + ry) / 2.0))
    return points


def _fit_line_robust(
    points: list[tuple[float, float]],
    sigma: float = 2.0,
) -> Optional[tuple[float, float]]:
    """Fit x = a*y + b with sigma-clip outlier rejection. Returns (a, b) or None."""
    if len(points) < 4:
        return None
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    try:
        coeffs = np.polyfit(ys, xs, 1)
    except Exception:
        return None

    residuals = np.abs(xs - np.polyval(coeffs, ys))
    std = float(np.std(residuals))
    if std > 0:
        inliers = residuals < sigma * std
        if inliers.sum() >= 4:
            try:
                coeffs = np.polyfit(ys[inliers], xs[inliers], 1)
            except Exception:
                pass

    return float(coeffs[0]), float(coeffs[1])


def _line_x_at_y(coeffs: tuple[float, float], y: float) -> float:
    return coeffs[0] * y + coeffs[1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_centering(
    img_bgr: np.ndarray,
    holes: Sequence,
    roi=None,
    tol_px: float = 0.0,
) -> Optional[CenteringResult]:
    """Measure how centered the hole pattern is between the metal sheet edges.

    Parameters
    ----------
    img_bgr:
        When roi is provided: the FULL-FRAME aligned image (both backlights
        must be visible in the frame even though they are outside the ROI).
        When roi is None: the image that contains visible sheet edges
        (legacy path, works when backlight is inside the frame).
    holes:
        Sequence of Hole objects with .x, .y, .r in ROI-relative coordinates.
    roi:
        Optional ROI object with .x, .y, .w, .h (full-frame coords).
        When provided, sheet edges are detected in search windows around the
        ROI boundaries so that the backlights (outside the ROI) are visible.
    tol_px:
        Tolerance in pixels. 0 = measure only, never triggers NOK.

    Returns None if no holes are provided or no edges can be detected at all.

    All coordinates in CenteringResult are in ROI space (same frame as holes).

    offset_px = margin_delta_px / 2, where:
      left_margin_px  = pattern_left_x  - left_x     (both ROI-relative)
      right_margin_px = right_x - pattern_right_x    (both ROI-relative)
      margin_delta_px = left_margin_px  - right_margin_px
    Positive offset_px means the pattern is shifted toward the right edge.
    """
    if not holes:
        return None

    h, w = img_bgr.shape[:2]

    if roi is not None:
        roi_x, roi_y = int(roi.x), int(roi.y)
        roi_w, roi_h = int(roi.w), int(roi.h)
        mid_y = roi_h / 2.0

        edge_left, edge_right = _detect_sheet_edges_in_windows(
            img_bgr, roi_x, roi_w, roi_y, roi_h
        )
        # Values are already in ROI-relative coords
        left_pts_list  = list(edge_left.values())
        right_pts_list = list(edge_right.values())
        img_h_for_bands = roi_h

    else:
        mid_y = h / 2.0
        edge_left, edge_right = _detect_edges_by_band(img_bgr)
        left_pts_list  = list(edge_left.values())
        right_pts_list = list(edge_right.values())
        img_h_for_bands = h

    n_left  = len(left_pts_list)
    n_right = len(right_pts_list)
    centering_reliable = (n_left >= _MIN_RELIABLE_BANDS and n_right >= _MIN_RELIABLE_BANDS)

    logger.debug(
        "compute_centering: %d left-band pts, %d right-band pts, reliable=%s",
        n_left, n_right, centering_reliable,
    )

    # Scalar edge positions: robust fitted line > median > give up
    left_coeffs  = _fit_line_robust(left_pts_list)
    right_coeffs = _fit_line_robust(right_pts_list)

    if left_coeffs is not None and right_coeffs is not None:
        left_x  = _line_x_at_y(left_coeffs,  mid_y)
        right_x = _line_x_at_y(right_coeffs, mid_y)
    elif left_pts_list and right_pts_list:
        left_x  = float(np.median([p[0] for p in left_pts_list]))
        right_x = float(np.median([p[0] for p in right_pts_list]))
    else:
        logger.debug("compute_centering: no edges detected, returning None")
        return None

    sheet_center_x = (left_x + right_x) / 2.0
    holes_center_x = float(np.mean([hh.x for hh in holes]))

    # Pattern bounds from actual detected holes (ROI-relative coords)
    pat_left, pat_right = _pattern_bounds_by_band(holes, img_h_for_bands)

    pattern_left_x  = float(min(hh.x - hh.r for hh in holes))
    pattern_right_x = float(max(hh.x + hh.r for hh in holes))

    left_margin_px  = pattern_left_x  - left_x
    right_margin_px = right_x - pattern_right_x
    margin_delta_px = left_margin_px  - right_margin_px
    offset_px       = margin_delta_px / 2.0
    within_tol      = (tol_px <= 0.0) or (abs(offset_px) <= tol_px)

    # Per-band margin statistics
    band_lm: list[float] = []
    band_rm: list[float] = []
    for i in range(_N_BANDS):
        if i in edge_left and i in edge_right and i in pat_left and i in pat_right:
            band_lm.append(pat_left[i][0]  - edge_left[i][0])
            band_rm.append(edge_right[i][0] - pat_right[i][0])

    left_margin_std  = float(np.std(band_lm)) if len(band_lm) >= 2 else 0.0
    right_margin_std = float(np.std(band_rm)) if len(band_rm) >= 2 else 0.0

    # Per-band point tuples for overlay (all ROI-relative)
    left_edge_points  = tuple(sorted(edge_left.values(),  key=lambda p: p[1]))
    right_edge_points = tuple(sorted(edge_right.values(), key=lambda p: p[1]))
    pattern_left_points  = tuple(sorted(pat_left.values(),  key=lambda p: p[1]))
    pattern_right_points = tuple(sorted(pat_right.values(), key=lambda p: p[1]))

    def _slope_deg(pts: list) -> float:
        """Slope angle from vertical (°). 0 = vertical. Sign: + leans right going down."""
        c = _fit_line_robust(pts)
        return float(np.degrees(np.arctan(c[0]))) if c is not None else 0.0

    left_edge_slope_deg    = _slope_deg(left_pts_list)
    right_edge_slope_deg   = _slope_deg(right_pts_list)
    pattern_left_slope_deg  = _slope_deg(list(pattern_left_points))
    pattern_right_slope_deg = _slope_deg(list(pattern_right_points))

    def _zigzag_residuals(pts_lists: list) -> tuple[float, float]:
        """Compute (std_px, max_px) of horizontal residuals from fitted line. Returns (0,0) if insufficient data."""
        residuals: list[float] = []
        for pts in pts_lists:
            if len(pts) < 3:
                continue
            c = _fit_line_robust(list(pts))
            if c is None:
                continue
            xs = np.array([p[0] for p in pts])
            ys = np.array([p[1] for p in pts])
            residuals.extend(np.abs(xs - (c[0] * ys + c[1])).tolist())
        if residuals:
            arr = np.array(residuals)
            return float(arr.std()), float(arr.max())
        return 0.0, 0.0

    # CHAPA (sheet edge) zigzag — camera/sheet vibration indicator
    chapa_zigzag_std_px, chapa_zigzag_max_px = _zigzag_residuals(
        [left_pts_list, right_pts_list]
    )
    # PATRON (hole pattern edge) zigzag — mechanical misalignment indicator
    pattern_zigzag_std_px, pattern_zigzag_max_px = _zigzag_residuals(
        [list(pattern_left_points), list(pattern_right_points)]
    )
    # PATRON CENTER zigzag — median X of all holes per band; catches internal zigzag
    center_pts = _pattern_center_by_band(pat_left, pat_right)
    pattern_center_zigzag_std_px, pattern_center_zigzag_max_px = _zigzag_residuals(
        [center_pts]
    )

    return CenteringResult(
        left_x=left_x,
        right_x=right_x,
        sheet_center_x=sheet_center_x,
        holes_center_x=holes_center_x,
        offset_px=offset_px,
        sheet_width_px=right_x - left_x,
        within_tol=within_tol,
        pattern_left_x=pattern_left_x,
        pattern_right_x=pattern_right_x,
        left_margin_px=left_margin_px,
        right_margin_px=right_margin_px,
        margin_delta_px=margin_delta_px,
        left_edge_points=left_edge_points,
        right_edge_points=right_edge_points,
        pattern_left_points=pattern_left_points,
        pattern_right_points=pattern_right_points,
        left_margin_std=left_margin_std,
        right_margin_std=right_margin_std,
        centering_reliable=centering_reliable,
        left_edge_slope_deg=left_edge_slope_deg,
        right_edge_slope_deg=right_edge_slope_deg,
        pattern_left_slope_deg=pattern_left_slope_deg,
        pattern_right_slope_deg=pattern_right_slope_deg,
        chapa_zigzag_std_px=chapa_zigzag_std_px,
        chapa_zigzag_max_px=chapa_zigzag_max_px,
        pattern_zigzag_std_px=pattern_zigzag_std_px,
        pattern_zigzag_max_px=pattern_zigzag_max_px,
        pattern_center_zigzag_std_px=pattern_center_zigzag_std_px,
        pattern_center_zigzag_max_px=pattern_center_zigzag_max_px,
    )
