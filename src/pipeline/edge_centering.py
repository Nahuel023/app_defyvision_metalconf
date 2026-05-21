"""
Detects the left and right edges of the metal sheet and measures whether
the hole pattern is centered between them.

Works directly on the ROI image — the metal sheet (dark) contrasts strongly
with the backlit background (bright) on both sides, making edges easy to find
via a column-brightness profile.

All coordinates are in ROI space, matching the hole positions returned by
detect_holes_from_mask.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class CenteringResult:
    left_x: float           # left metal edge X  (px, ROI coords)
    right_x: float          # right metal edge X (px, ROI coords)
    sheet_center_x: float   # midpoint between edges
    holes_center_x: float   # mean X of detected holes
    offset_px: float        # holes_center_x - sheet_center_x  (+ = holes shifted right)
    sheet_width_px: float   # right_x - left_x
    within_tol: bool        # |offset_px| <= tol_px  (always True when tol_px == 0)


def _detect_metal_edges(img_bgr: np.ndarray) -> Optional[tuple[float, float]]:
    """
    Returns (left_edge_x, right_edge_x) in ROI coordinates.

    Uses the 20th-percentile column brightness to capture the dark metal body
    even where bright holes raise the average. The backlit background on both
    sides is much brighter than the metal, producing a clear threshold.
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # 20th percentile per column: dark metal body dominates even with bright holes
    col_profile = np.percentile(gray, 20, axis=0).astype(np.float32)

    # Smooth to remove single-column noise (no external deps — uses OpenCV blur)
    k = max(5, w // 30)
    k = k + 1 if k % 2 == 0 else k
    col_smooth = cv2.GaussianBlur(col_profile.reshape(1, -1), (k, 1), 0).ravel()

    # Background (outside metal) is the brightest region
    bg_level = float(np.percentile(col_smooth, 95))
    metal_thresh = bg_level * 0.70

    is_metal = col_smooth < metal_thresh
    metal_cols = np.where(is_metal)[0]

    if len(metal_cols) < max(5, int(w * 0.05)):
        return None

    return float(metal_cols[0]), float(metal_cols[-1])


def compute_centering(
    img_bgr: np.ndarray,
    holes_xs: Sequence[float],
    tol_px: float = 0.0,
) -> Optional[CenteringResult]:
    """
    Measures how centered the hole pattern is between the metal sheet edges.

    Returns None if edge detection fails or no holes are provided.
    tol_px: tolerance in pixels. 0 = measure only, never triggers NOK.
    """
    if not holes_xs:
        return None

    edges = _detect_metal_edges(img_bgr)
    if edges is None:
        return None

    left_x, right_x = edges
    sheet_center_x  = (left_x + right_x) / 2.0
    holes_center_x  = float(np.mean(list(holes_xs)))
    offset_px       = holes_center_x - sheet_center_x
    within_tol      = (tol_px <= 0.0) or (abs(offset_px) <= tol_px)

    return CenteringResult(
        left_x=left_x,
        right_x=right_x,
        sheet_center_x=sheet_center_x,
        holes_center_x=holes_center_x,
        offset_px=offset_px,
        sheet_width_px=right_x - left_x,
        within_tol=within_tol,
    )
