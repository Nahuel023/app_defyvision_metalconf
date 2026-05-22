"""
Detects the left and right edges of the metal sheet and measures whether
the hole pattern is centered between them.

Works directly on the ROI image — the metal sheet (dark) contrasts strongly
with the backlit background (bright) on both sides, making edges easy to find
via a column-brightness profile.

All coordinates are in ROI space, matching the hole positions returned by
detect_holes_from_mask.

Edge detection strategy:
  - Image is divided into N_BANDS horizontal bands.
  - Per band: 20th-percentile column profile → threshold → find metal columns.
  - A robust line (sigma-clip polyfit) is fitted to the per-band edge points.
  - The scalar left_x / right_x are evaluated at mid-height from that line.
  - Pattern bounds use the actual detected holes per band (hole.x ± hole.r).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

_N_BANDS = 16
_MIN_RELIABLE_BANDS = 6   # minimum bands needed to declare centering reliable


@dataclass(frozen=True)
class CenteringResult:
    left_x: float           # left metal edge X  (px, ROI coords) — fitted line at mid-height
    right_x: float          # right metal edge X (px, ROI coords)
    sheet_center_x: float   # midpoint between sheet edges
    holes_center_x: float   # mean X of detected holes
    offset_px: float        # margin_delta_px / 2  (+ = pattern shifted right)
    sheet_width_px: float   # right_x - left_x
    within_tol: bool        # |offset_px| <= tol_px  (always True when tol_px == 0)
    pattern_left_x: float   # min(hole.x - hole.r) — left physical bound of detected pattern
    pattern_right_x: float  # max(hole.x + hole.r) — right physical bound of detected pattern
    left_margin_px: float   # pattern_left_x - sheet_left_x
    right_margin_px: float  # sheet_right_x - pattern_right_x
    margin_delta_px: float  # left_margin_px - right_margin_px (>0 = pattern shifted right)

    # Per-band real measurement points (tuple of (x, y) sorted by y)
    left_edge_points: tuple = field(default_factory=tuple)
    right_edge_points: tuple = field(default_factory=tuple)
    pattern_left_points: tuple = field(default_factory=tuple)
    pattern_right_points: tuple = field(default_factory=tuple)

    # Per-band margin variation (std dev across bands, 0 if < 2 matched bands)
    left_margin_std: float = 0.0
    right_margin_std: float = 0.0

    # False if too few bands were detected to trust the measurement
    centering_reliable: bool = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
    """
    Returns per-band left/right metal edge points, keyed by band index.

    Returns: (left_dict, right_dict) where each dict maps band_idx -> (x, cy).
    Bands with no clear metal edge are omitted.
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    metal_thresh = _background_threshold(gray, w)
    band_h = h / n_bands
    min_metal_cols = max(5, int(w * 0.05))

    left_dict: dict[int, tuple[float, float]] = {}
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
        left_dict[i] = (float(metal_cols[0]), cy)
        right_dict[i] = (float(metal_cols[-1]), cy)

    return left_dict, right_dict


def _pattern_bounds_by_band(
    holes: Sequence,
    img_h: int,
    n_bands: int = _N_BANDS,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Per-band left/right physical bounds using detected hole positions."""
    band_h = img_h / n_bands
    left_dict: dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0 = i * band_h
        y1 = (i + 1) * band_h
        band_holes = [hh for hh in holes if y0 <= hh.y < y1]
        if not band_holes:
            continue
        cy = (y0 + y1) / 2.0
        left_dict[i] = (float(min(hh.x - hh.r for hh in band_holes)), cy)
        right_dict[i] = (float(max(hh.x + hh.r for hh in band_holes)), cy)

    return left_dict, right_dict


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


def _detect_metal_edges_full(img_bgr: np.ndarray) -> Optional[tuple[float, float]]:
    """Full-image column profile fallback. Returns (left_x, right_x) or None."""
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    col_profile = np.percentile(gray, 20, axis=0).astype(np.float32)
    k = max(5, w // 30)
    k = k + 1 if k % 2 == 0 else k
    col_smooth = cv2.GaussianBlur(col_profile.reshape(1, -1), (k, 1), 0).ravel()
    bg_level = float(np.percentile(col_smooth, 95))
    metal_thresh = bg_level * 0.70
    metal_cols = np.where(col_smooth < metal_thresh)[0]
    if len(metal_cols) < max(5, int(w * 0.05)):
        return None
    return float(metal_cols[0]), float(metal_cols[-1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_centering(
    img_bgr: np.ndarray,
    holes: Sequence,
    tol_px: float = 0.0,
) -> Optional[CenteringResult]:
    """
    Measures how centered the hole pattern is between the metal sheet edges.

    holes: sequence of Hole objects (must have .x and .r attributes).
    Returns None if edge detection fails or no holes are provided.
    tol_px: tolerance in pixels. 0 = measure only, never triggers NOK.

    offset_px = margin_delta_px / 2, where:
      left_margin_px  = pattern_left_x  - sheet_left_x
      right_margin_px = sheet_right_x   - pattern_right_x
      margin_delta_px = left_margin_px  - right_margin_px
    Positive offset_px means the pattern is shifted toward the right edge.
    """
    if not holes:
        return None

    h, w = img_bgr.shape[:2]
    mid_y = h / 2.0

    # --- Per-band sheet edge detection ---
    edge_left, edge_right = _detect_edges_by_band(img_bgr)
    n_left = len(edge_left)
    n_right = len(edge_right)
    centering_reliable = (n_left >= _MIN_RELIABLE_BANDS and n_right >= _MIN_RELIABLE_BANDS)

    # --- Scalar left_x / right_x from robust fitted line ---
    left_pts_list = list(edge_left.values())
    right_pts_list = list(edge_right.values())

    left_coeffs = _fit_line_robust(left_pts_list)
    right_coeffs = _fit_line_robust(right_pts_list)

    if left_coeffs is not None and right_coeffs is not None:
        left_x = _line_x_at_y(left_coeffs, mid_y)
        right_x = _line_x_at_y(right_coeffs, mid_y)
    elif left_pts_list and right_pts_list:
        left_x = float(np.median([p[0] for p in left_pts_list]))
        right_x = float(np.median([p[0] for p in right_pts_list]))
    else:
        edges = _detect_metal_edges_full(img_bgr)
        if edges is None:
            return None
        left_x, right_x = edges

    sheet_center_x = (left_x + right_x) / 2.0
    holes_center_x = float(np.mean([hh.x for hh in holes]))

    # --- Per-band pattern bounds from real detected holes ---
    pat_left, pat_right = _pattern_bounds_by_band(holes, h)

    # --- Overall pattern bounds ---
    pattern_left_x = float(min(hh.x - hh.r for hh in holes))
    pattern_right_x = float(max(hh.x + hh.r for hh in holes))

    left_margin_px = pattern_left_x - left_x
    right_margin_px = right_x - pattern_right_x
    margin_delta_px = left_margin_px - right_margin_px
    offset_px = margin_delta_px / 2.0
    within_tol = (tol_px <= 0.0) or (abs(offset_px) <= tol_px)

    # --- Per-band margin statistics (bands where we have both edge and pattern) ---
    band_lm: list[float] = []
    band_rm: list[float] = []
    for i in range(_N_BANDS):
        if i in edge_left and i in edge_right and i in pat_left and i in pat_right:
            band_lm.append(pat_left[i][0] - edge_left[i][0])
            band_rm.append(edge_right[i][0] - pat_right[i][0])

    left_margin_std = float(np.std(band_lm)) if len(band_lm) >= 2 else 0.0
    right_margin_std = float(np.std(band_rm)) if len(band_rm) >= 2 else 0.0

    # --- Convert per-band dicts to sorted tuples for overlay ---
    left_edge_points = tuple(sorted(edge_left.values(), key=lambda p: p[1]))
    right_edge_points = tuple(sorted(edge_right.values(), key=lambda p: p[1]))
    pattern_left_points = tuple(sorted(pat_left.values(), key=lambda p: p[1]))
    pattern_right_points = tuple(sorted(pat_right.values(), key=lambda p: p[1]))

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
    )
