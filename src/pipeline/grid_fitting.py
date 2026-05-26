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
    """Assign each hole to its (col, row) grid cell index.

    Uses round-half-up (int(x + 0.5)) instead of Python's banker's rounding
    (round()) to avoid collisions when a coordinate falls exactly at mid-period.
    """
    return [
        (int((x - phase_x) / dx + 0.5), int((y - phase_y) / dy + 0.5))
        for x, y in points
    ]


def _fit_affine_to_grid(
    detected_xy: np.ndarray,
    cells: list[tuple[int, int]],
    dx: float,
    dy: float,
    origin_x: float,
    origin_y: float,
    tol_affine: float,
    min_matches: int,
    margin: float,
    img_w: int,
    img_h: int,
) -> np.ndarray | None:
    """
    Refine grid expected positions with a lightweight affine correction.

    After the global phase estimation places expected holes at
    (origin_x + ci*dx, origin_y + cj*dy), sheet tilt / perspective / local
    drift can leave holes in the border zones consistently outside tol_xy_px.

    This function matches detected holes to the initial expected positions
    (within tol_affine), fits a 2-D affine map:
        detected_xy ≈ W[:2].T @ [ci*dx, cj*dy] + W[2]
    via least squares, sanity-checks the result, and returns corrected expected
    positions for all cells as a (n_cells, 2) float32 array.

    Returns None if there are too few matches or the affine is implausible
    (scale/shear outside safe bounds), in which case the caller falls back to
    the phase-grid positions.
    """
    n_cells = len(cells)
    if n_cells == 0 or len(detected_xy) == 0:
        return None

    ci_arr = np.array([ci for ci, _ in cells], dtype=np.float32)
    cj_arr = np.array([cj for _, cj in cells], dtype=np.float32)
    init_x = origin_x + ci_arr * dx
    init_y = origin_y + cj_arr * dy

    # Only consider in-frame cells for the matching
    in_frame = (
        (init_x >= margin) & (init_x <= img_w - margin) &
        (init_y >= margin) & (init_y <= img_h - margin)
    )
    in_frame_idx = np.where(in_frame)[0]
    if len(in_frame_idx) < min_matches:
        return None

    exp_in = np.stack([init_x[in_frame_idx], init_y[in_frame_idx]], axis=1)  # (n_in, 2)

    # Greedy closest-first matching: detected → nearest in-frame expected within tol_affine
    diff2 = (detected_xy[:, None, :] - exp_in[None, :, :]) ** 2   # (n_det, n_in, 2)
    dist2 = diff2.sum(axis=2)                                       # (n_det, n_in)
    tol2  = tol_affine ** 2

    used_exp = np.zeros(len(in_frame_idx), dtype=bool)
    src_det:  list[np.ndarray]   = []
    src_cell: list[list[float]]  = []

    for det_i in np.argsort(dist2.min(axis=1)):
        row = dist2[det_i].copy()
        row[used_exp] = np.inf
        best_j = int(np.argmin(row))
        if row[best_j] <= tol2:
            used_exp[best_j] = True
            src_det.append(detected_xy[det_i])
            ci, cj = cells[in_frame_idx[best_j]]
            src_cell.append([ci * dx, cj * dy])

    if len(src_det) < min_matches:
        return None

    # Least-squares affine: detected_xy ≈ X_aug @ W  where X_aug = [ci*dx, cj*dy, 1]
    X = np.array(src_cell, dtype=np.float64)
    Y = np.array(src_det,  dtype=np.float64)
    X_aug = np.hstack([X, np.ones((len(X), 1), dtype=np.float64)])
    W, _, _, _ = np.linalg.lstsq(X_aug, Y, rcond=None)  # (3, 2)

    # W[0,:] = how ci*dx maps to [out_x, out_y] → W[0,0] ≈ 1, W[0,1] ≈ 0
    # W[1,:] = how cj*dy maps to [out_x, out_y] → W[1,0] ≈ 0, W[1,1] ≈ 1
    scale_x  = float(W[0, 0])
    scale_y  = float(W[1, 1])
    shear_xy = float(W[0, 1])
    shear_yx = float(W[1, 0])
    if not (0.85 <= scale_x <= 1.15 and 0.85 <= scale_y <= 1.15
            and abs(shear_xy) < 0.15 and abs(shear_yx) < 0.15):
        return None

    # Apply affine to ALL cells; margin filter is applied by the caller
    all_ci = np.array([ci * dx for ci, _ in cells], dtype=np.float64)
    all_cj = np.array([cj * dy for _, cj in cells], dtype=np.float64)
    all_aug = np.column_stack([all_ci, all_cj, np.ones(n_cells, dtype=np.float64)])
    corrected = (all_aug @ W).astype(np.float32)  # (n_cells, 2)
    return corrected


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
    tol_affine: float = 0.0,
    min_affine_matches: int = 12,
) -> list[tuple[float, float]]:
    """
    Return expected hole positions in the CURRENT frame.

    Step 1 — Global phase estimation (X then Y, 2-D scan):
        Finds the integer-pixel origin that maximises the count of detected holes
        within the half-period tolerance of a cell position.

    Step 2 — Optional affine refinement (tol_affine > 0):
        After the global phase places initial expected positions, sheet tilt /
        perspective / curvature can leave a few holes near the borders outside
        tol_xy_px.  _fit_affine_to_grid matches detected holes to the initial
        positions, fits a lightweight affine map, and returns corrected positions
        if the fit is plausible (scale 0.85–1.15, shear < 0.15).  Falls back to
        phase-grid positions if the fit fails.

    Works at any sheet position — no fixed absolute coordinates.
    """
    if len(detected_xy) == 0 or not cells:
        return []

    # ── Step 1: global X-phase ──────────────────────────────────────────────
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

    # ── Step 1: global Y-phase (2-D scan using already-fixed origin_x) ─────
    cj_arr = np.array([cj for _, cj in cells], dtype=np.float32)
    tol_y   = max(dy * 0.45, 4.0)

    exp_xs_all  = best_phase_x + ci_arr * dx          # (n_cells,)
    det_xs_col  = detected_xy[:, 0:1]                 # (n_det, 1)
    x_match     = np.abs(det_xs_col - exp_xs_all[None, :]) <= tol_x  # (n_det, n_cells)

    best_phase_y = estimate_phase(detected_xy[:, 1], dy)
    best_count   = -1
    for phase_candidate in np.arange(0.0, dy, 1.0):
        exp_ys = phase_candidate + cj_arr * dy
        valid  = (exp_ys >= margin) & (exp_ys <= img_h - margin)
        if not valid.any():
            continue
        det_ys_col = detected_xy[:, 1:2]
        y_match    = np.abs(det_ys_col - exp_ys[None, :]) <= tol_y
        both  = x_match & y_match & valid[None, :]
        count = int(both.any(axis=1).sum())
        if count > best_count:
            best_count   = count
            best_phase_y = phase_candidate

    origin_y = best_phase_y

    # ── Step 2: optional affine refinement ─────────────────────────────────
    corrected_xy: np.ndarray | None = None
    if tol_affine > 0:
        corrected_xy = _fit_affine_to_grid(
            detected_xy, cells, dx, dy,
            origin_x, origin_y,
            tol_affine, min_affine_matches,
            margin, img_w, img_h,
        )

    # ── Build final expected positions (with deduplication + margin filter) ─
    result: list[tuple[float, float]] = []
    seen:   set[tuple[int, int]]      = set()
    for k, (ci, cj) in enumerate(cells):
        if corrected_xy is not None:
            ex = float(corrected_xy[k, 0])
            ey = float(corrected_xy[k, 1])
        else:
            ex = origin_x + ci * dx
            ey = origin_y + cj * dy
        key = (round(ex), round(ey))
        if key in seen:
            continue
        if margin <= ex <= img_w - margin and margin <= ey <= img_h - margin:
            seen.add(key)
            result.append((ex, ey))
    return result
