"""
Position-invariant grid fitting for hole inspection.

The sheet arrives at a different position each cycle. This module estimates
the grid structure (spacing + topology) once at pattern-build time, and then
at inspection time finds where that grid is located in each frame — without
any fixed absolute reference.
"""
from __future__ import annotations
import numpy as np


def estimate_spacing(coords: np.ndarray, min_spacing: float = 30.0) -> float:
    """Mode of pairwise differences → fundamental grid spacing in one axis."""
    diffs = np.abs(coords[:, None] - coords[None, :]).ravel()
    diffs = diffs[diffs >= min_spacing]
    if len(diffs) == 0:
        return max(1.0, min_spacing)
    bins = np.round(diffs / 2.0).astype(np.int32)
    mode = int(np.bincount(bins[bins > 0]).argmax())
    return float(max(1, mode) * 2.0)


def estimate_phase(coords: np.ndarray, spacing: float) -> float:
    """Mode of (coords % spacing) → fractional grid origin (0 … spacing)."""
    fracs = coords % spacing
    bin_sz = 2.0
    n_bins = max(1, round(spacing / bin_sz))
    bins = (np.round(fracs / bin_sz).astype(np.int32)) % n_bins
    return float(int(np.bincount(bins).argmax()) * bin_sz)


def assign_cells(
    points: list[tuple[float, float]],
    dx: float,
    dy: float,
    phase_x: float,
    phase_y: float,
) -> list[tuple[int, int]]:
    """Assign each hole to its (col, row) grid cell index."""
    return [
        (round((x - phase_x) / dx), round((y - phase_y) / dy))
        for x, y in points
    ]



def grid_compare_points(
    detected_xy: np.ndarray,
    cells: list[tuple[int, int]],
    dx: float,
    dy: float,
    phase_ref_x: float,
    phase_ref_y: float,
    transform: np.ndarray | None,
    img_w: int,
    img_h: int,
    margin: float,
) -> list[tuple[float, float]]:
    """
    Return expected hole positions in the CURRENT frame.

    Combines:
    - Phase estimation from detected holes (fractional grid origin, per-frame)
    - Integer offset from the voting-based transform (resolves aliasing)

    Works at any sheet position — no fixed absolute coordinates.
    """
    if len(detected_xy) == 0 or not cells:
        return []

    # X-phase: for this staggered grid the offset is encoded in integer ci values
    # (odd/even ci alternating rows), so ALL holes satisfy x % dx == phase_ref_x % dx.
    # Re-estimating per-frame safely tracks small lateral drift without bimodal ambiguity.
    # Add a scan (identical to the Y scan below) for robustness on blurry transition frames.
    ci_arr = np.array([ci for ci, _ in cells], dtype=np.float32)
    det_xs  = detected_xy[:, 0]
    tol_x   = max(dx * 0.45, 4.0)

    best_phase_x = estimate_phase(detected_xy[:, 0], dx)
    best_count_x = -1
    for px_cand in np.arange(0.0, dx, 1.0):
        exp_xs = px_cand + ci_arr * dx
        valid_x = (exp_xs >= margin) & (exp_xs <= img_w - margin)
        if not valid_x.any():
            continue
        exp_xs_v = exp_xs[valid_x]
        diffs_x = np.abs(det_xs[:, None] - exp_xs_v[None, :])
        count_x = int((diffs_x.min(axis=1) <= tol_x).sum())
        if count_x > best_count_x:
            best_count_x = count_x
            best_phase_x = px_cand

    origin_x = best_phase_x

    # Y-phase: scan over [0, dy) using 2D tolerance (X and Y simultaneously) with the
    # already-determined origin_x so that holes from adjacent rows cannot be false-matched
    # in the Y-axis alone.  This is robust to transition frames where many holes appear
    # at intermediate Y positions due to sheet motion.
    cj_arr = np.array([cj for _, cj in cells], dtype=np.float32)
    tol_x   = max(dx * 0.45, 4.0)
    tol_y   = max(dy * 0.45, 4.0)

    # Pre-compute expected X positions for all cells (fixed once origin_x is known)
    exp_xs_all = best_phase_x + ci_arr * dx            # shape (n_cells,)

    # Build a boolean mask per detected hole: does its X match any cell's X?
    det_xs_col = detected_xy[:, 0:1]                   # (n_det, 1)
    x_match = np.abs(det_xs_col - exp_xs_all[None, :]) <= tol_x  # (n_det, n_cells)

    best_phase_y = estimate_phase(detected_xy[:, 1], dy)   # initial guess (fallback)
    best_count   = -1
    for phase_candidate in np.arange(0.0, dy, 1.0):
        exp_ys = phase_candidate + cj_arr * dy         # (n_cells,)
        # Keep only cells inside the frame
        valid = (exp_ys >= margin) & (exp_ys <= img_h - margin)
        if not valid.any():
            continue
        det_ys_col = detected_xy[:, 1:2]              # (n_det, 1)
        y_match = np.abs(det_ys_col - exp_ys[None, :]) <= tol_y   # (n_det, n_cells)
        # A detected hole matches if it is within tol_x in X AND tol_y in Y of the same cell
        both = x_match & y_match & valid[None, :]      # (n_det, n_cells)
        count = int(both.any(axis=1).sum())
        if count > best_count:
            best_count  = count
            best_phase_y = phase_candidate

    origin_y = best_phase_y

    # Deduplicate: multiple stored cells can map to the same (ex,ey) on staggered
    # grids where adjacent holes round to the same (ci,cj). Keeping duplicates
    # causes compare_missing_only to claim the same detected hole twice, turning
    # every second occurrence into a spurious miss.
    result: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for ci, cj in cells:
        ex = origin_x + ci * dx
        ey = origin_y + cj * dy
        key = (round(ex), round(ey))
        if key in seen:
            continue
        if margin <= ex <= img_w - margin and margin <= ey <= img_h - margin:
            seen.add(key)
            result.append((float(ex), float(ey)))
    return result
