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


def rotate_points(pts: np.ndarray, deg: float, cx: float, cy: float) -> np.ndarray:
    """Rota un array (N,2) `deg` grados alrededor de (cx, cy). Devuelve float32."""
    if pts is None or len(pts) == 0:
        return pts
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    x = pts[:, 0] - cx
    y = pts[:, 1] - cy
    return np.stack([c * x - s * y + cx, s * x + c * y + cy], axis=1).astype(np.float32)


def estimate_lattice_tilt_deg(
    detected_xy: np.ndarray,
    dx: float,
    row_dy_tol: float | None = None,
    dy: float | None = None,
) -> float:
    """Estima la rotación (tilt) de la grilla a partir de los agujeros detectados.

    Para cada agujero busca su vecino más cercano hacia la derecha dentro de la misma
    fila (Δx en ~[0.5·dx, 1.4·dx], |Δy| < row_dy_tol) y toma el ángulo de ese vector.
    La mediana de esos ángulos es la inclinación de las filas respecto de la horizontal.

    Es más robusto y directo que medir el borde de la chapa: refleja la orientación real
    del patrón de agujeros, que es lo que el matching necesita. Devuelve grados
    (positivo = filas inclinadas hacia abajo a la derecha). NaN si hay muy pocos agujeros.
    """
    if detected_xy is None or len(detected_xy) < 8 or dx <= 0:
        return float("nan")
    if row_dy_tol is None:
        if dy is not None and dy > 0:
            row_dy_tol = max(3.0, float(dy) * 0.6)
        else:
            row_dy_tol = 20.0
    xs = detected_xy[:, 0]
    ys = detected_xy[:, 1]
    lo, hi = 0.5 * dx, 1.4 * dx
    # Matriz de diferencias por pares (vectorizado): equivalente al loop punto-a-punto
    # original pero sin el overhead de Python por agujero — con ~150-200 agujeros
    # (microperforado) esto corre ~20x mas rapido en el mismo frame.
    ddx = xs[None, :] - xs[:, None]
    ddy = ys[None, :] - ys[:, None]
    mask = (ddx > lo) & (ddx < hi) & (np.abs(ddy) < row_dy_tol)
    row_has = mask.any(axis=1)
    if not row_has.any():
        return float("nan")
    ddx_masked = np.where(mask, ddx, np.inf)
    j_idx = np.argmin(ddx_masked, axis=1)
    rows = np.where(row_has)[0]
    angles = np.degrees(np.arctan2(ddy[rows, j_idx[rows]], ddx[rows, j_idx[rows]]))
    return float(np.median(angles))


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
    stagger_x_odd: float = 0.0,
) -> list[tuple[int, int]]:
    """Assign each hole to its (col, row) grid cell index.

    Uses round-half-up (int(x + 0.5)) instead of Python's banker's rounding
    (round()) to avoid collisions when a coordinate falls exactly at mid-period.

    When stagger_x_odd != 0, odd-cj rows have their X origin shifted by
    stagger_x_odd pixels relative to even-cj rows.  The CI assignment
    subtracts this shift before dividing by dx so that staggered holes map
    to the correct column index, consistent with how grid_compare_points
    generates expected positions.
    """
    result: list[tuple[int, int]] = []
    for x, y in points:
        cj = int((y - phase_y) / dy + 0.5)
        if stagger_x_odd != 0.0 and cj % 2 == 1:
            # Odd row: X origin is (phase_x + stagger_x_odd) % dx
            x_origin_odd = (phase_x + stagger_x_odd) % dx
            ci = int((x - x_origin_odd) / dx + 0.5)
        else:
            ci = int((x - phase_x) / dx + 0.5)
        result.append((ci, cj))
    return result


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
    stagger_x_odd: float = 0.0,
) -> np.ndarray | None:
    """
    Refine grid expected positions with a lightweight affine correction.

    After the global phase estimation places expected holes at
    (origin_x + ci*dx, origin_y + cj*dy), sheet tilt / perspective / local
    drift can leave holes in the border zones consistently outside tol_xy_px.

    This function matches detected holes to the initial expected positions
    (within tol_affine), fits a 2-D affine map using stagger-adjusted source
    coordinates so that staggered grids (where odd-cj rows have a shifted X
    origin) are handled correctly:
        source_x = ci*dx + (cj%2)*stagger_x_odd
        source_y = cj*dy
        detected_xy ≈ W[:2].T @ [source_x, source_y] + W[2]
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
    parity = (cj_arr % 2).astype(np.float32)

    # init positions account for stagger on odd-cj rows
    origins_x = (origin_x + parity * stagger_x_odd) % dx if stagger_x_odd != 0.0 \
        else np.full(n_cells, origin_x, dtype=np.float32)
    init_x = origins_x + ci_arr * dx
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
            # Stagger-adjusted source: absorbs the cj-parity X offset so
            # even and odd rows fit the same affine without spurious shear.
            src_cell.append([ci * dx + (cj % 2) * stagger_x_odd, cj * dy])

    if len(src_det) < min_matches:
        return None

    # Least-squares affine: detected_xy ≈ X_aug @ W
    # where X_aug = [ci*dx + (cj%2)*stagger, cj*dy, 1]
    X = np.array(src_cell, dtype=np.float64)
    Y = np.array(src_det,  dtype=np.float64)
    X_aug = np.hstack([X, np.ones((len(X), 1), dtype=np.float64)])
    W, _, _, _ = np.linalg.lstsq(X_aug, Y, rcond=None)  # (3, 2)

    # W[0,:] = how (ci*dx + stagger*parity) maps to [out_x, out_y] → W[0,0] ≈ 1
    # W[1,:] = how cj*dy maps to [out_x, out_y] → W[1,1] ≈ actual_dy/stored_dy
    scale_x  = float(W[0, 0])
    scale_y  = float(W[1, 1])
    shear_xy = float(W[0, 1])
    shear_yx = float(W[1, 0])
    if not (0.85 <= scale_x <= 1.15 and 0.85 <= scale_y <= 1.15
            and abs(shear_xy) < 0.15 and abs(shear_yx) < 0.15):
        return None

    # Apply affine to ALL cells using stagger-adjusted source
    all_ci = np.array([ci * dx + (cj % 2) * stagger_x_odd for ci, cj in cells], dtype=np.float64)
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
    stagger_x_odd: float = 0.0,
    margin_x: float | None = None,
    margin_y: float | None = None,
) -> tuple[list[tuple[float, float]], list[tuple[int, int]]]:
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

    Returns:
        (points, cells_out) — parallel lists where cells_out[i] = (ci, cj) for
        points[i].  The cell indices are the canonical grid coordinates that
        identify which physical punch produced each expected hole — used by
        MachineStopDetector to track persistent missing holes by column (ci)
        rather than by pixel X, so the same defect is recognised across frames
        even as the sheet advances vertically.
    """
    if len(detected_xy) == 0 or not cells:
        return []

    mx = margin if margin_x is None else margin_x
    my = margin if margin_y is None else margin_y

    # ── Step 1: global X-phase ────────────────────────────────────────
    ci_arr  = np.array([ci for ci, _ in cells], dtype=np.float32)
    cj_arr  = np.array([cj for _, cj in cells], dtype=np.float32)
    parity  = (cj_arr % 2).astype(np.float32)         # 0=even, 1=odd
    det_xs  = detected_xy[:, 0]
    # For staggered grids, use tight scan tolerance so even/odd row phases don't
    # cross-contaminate each other during the phase search.  The stagger places
    # the two row types ~|stagger_x_odd| px apart; using |stagger|/4 keeps
    # the tolerance well below the half-separation threshold (~|stagger|/2).
    if stagger_x_odd != 0.0:
        tol_x = max(abs(stagger_x_odd) / 4.0, 5.0)
    else:
        tol_x = max(dx * 0.45, 4.0)

    best_phase_x = estimate_phase(detected_xy[:, 0], dx)
    best_count_x = -1
    best_dist_x = float("inf")
    for px_cand in np.arange(0.0, dx, 1.0):
        # Even-cj rows use px_cand; odd-cj rows have origin (px_cand+stagger)%dx
        origins = (px_cand + parity * stagger_x_odd) % dx
        exp_xs = origins + ci_arr * dx
        valid_x = (exp_xs >= mx) & (exp_xs <= img_w - mx)
        if not valid_x.any():
            continue
        exp_xs_v = exp_xs[valid_x]
        diffs_x = np.abs(det_xs[:, None] - exp_xs_v[None, :])
        min_diffs = diffs_x.min(axis=1)
        count_x = int((min_diffs <= tol_x).sum())
        total_dist = float(min_diffs[min_diffs <= tol_x].sum())
        # Primary: maximize match count; secondary: minimize total distance
        if count_x > best_count_x or (count_x == best_count_x and total_dist < best_dist_x):
            best_count_x = count_x
            best_dist_x = total_dist
            best_phase_x = px_cand

    origin_x = best_phase_x

    # ── Step 1: global Y-phase (2-D scan, origin_x ya fijo) ───────────
    tol_y   = max(dy * 0.45, 4.0)

    # Per-cell expected X, accounting for stagger on odd-cj rows (with modulo)
    origins_all = (best_phase_x + parity * stagger_x_odd) % dx
    exp_xs_all  = origins_all + ci_arr * dx                            # (n_cells,)
    det_xs_col  = detected_xy[:, 0:1]                 # (n_det, 1)
    x_match     = np.abs(det_xs_col - exp_xs_all[None, :]) <= tol_x  # (n_det, n_cells)

    best_phase_y = estimate_phase(detected_xy[:, 1], dy)
    best_count   = -1
    best_dist_y  = float("inf")
    for phase_candidate in np.arange(0.0, dy, 1.0):
        exp_ys = phase_candidate + cj_arr * dy
        valid  = (exp_ys >= my) & (exp_ys <= img_h - my)
        if not valid.any():
            continue
        det_ys_col = detected_xy[:, 1:2]
        y_match    = np.abs(det_ys_col - exp_ys[None, :]) <= tol_y
        both  = x_match & y_match & valid[None, :]
        matched = both.any(axis=1)
        count = int(matched.sum())
        # For tied counts, prefer phase that minimises total matched distance
        if count > 0:
            # matched_min_dist: for each matched detected hole, min dist to any matching expected
            exp_ys_valid = exp_ys[valid]
            y_diffs_matched = np.abs(detected_xy[matched, 1:2] - exp_ys_valid[None, :])
            total_dist_y = float(y_diffs_matched.min(axis=1).sum())
        else:
            total_dist_y = float("inf")
        if count > best_count or (count == best_count and total_dist_y < best_dist_y):
            best_count   = count
            best_dist_y  = total_dist_y
            best_phase_y = phase_candidate

    origin_y = best_phase_y

    # ── Step 2: optional affine refinement ────────────────────────────
    corrected_xy: np.ndarray | None = None
    if tol_affine > 0:
        corrected_xy = _fit_affine_to_grid(
            detected_xy, cells, dx, dy,
            origin_x, origin_y,
            tol_affine, min_affine_matches,
            margin, img_w, img_h,
            stagger_x_odd=stagger_x_odd,
        )

    # ── Build final expected positions (dedup + margen) ───────────────
    result:       list[tuple[float, float]] = []
    result_cells: list[tuple[int, int]]     = []
    seen:         set[tuple[int, int]]      = set()
    for k, (ci, cj) in enumerate(cells):
        if corrected_xy is not None:
            ex = float(corrected_xy[k, 0])
            ey = float(corrected_xy[k, 1])
        else:
            # Apply X-stagger for odd-cj rows (modular phase)
            x_origin = (origin_x + stagger_x_odd) % dx if cj % 2 == 1 else origin_x
            ex = x_origin + ci * dx
            ey = origin_y + cj * dy
        key = (round(ex), round(ey))
        if key in seen:
            continue
        if mx <= ex <= img_w - mx and my <= ey <= img_h - my:
            seen.add(key)
            result.append((ex, ey))
            result_cells.append((ci, cj))
    return result, result_cells
