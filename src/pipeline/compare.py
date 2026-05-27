from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

try:
    from scipy.optimize import linear_sum_assignment as _lsa
    _SCIPY_AVAILABLE = True
except ImportError:
    _lsa = None
    _SCIPY_AVAILABLE = False


@dataclass(frozen=True)
class CompareReport:
    expected: int
    detected: int
    missing: int
    status: str  # "OK" o "NOK"
    missing_points: List[Tuple[float, float]]
    matched_detected_idx: List[int]  # índices de detectados que matchearon (para overlay)
    extra: int = 0
    extra_points: List[Tuple[float, float]] = field(default_factory=list)
    # Grid cell coordinates (ci, cj) for each missing expected hole.
    # Populated when expected_cells is passed to compare_missing_only.
    # Used by MachineStopDetector to track persistent defects by column (ci)
    # instead of pixel X — so the same broken punch is recognised across
    # frames even as the sheet advances vertically.
    missing_cells: List[Tuple[int, int]] = field(default_factory=list)


def compare_missing_only(
    expected_points: List[Tuple[float, float]],
    detected_points: List[Tuple[float, float]],
    tol_xy_px: float = 8.0,
    max_missing: int = 0,
    max_extra: int = -1,
    extra_min_dist_factor: float = 0.0,
    expected_cells: List[Tuple[int, int]] | None = None,
    use_hungarian: bool = False,
) -> CompareReport:
    """
    Match each expected hole to the nearest free detected hole within tol_xy_px.

    Two matching strategies:
    - Greedy (default): closest-first — fast, O(n log n), occasionally steals
      a detected hole from a closer expected when tol_xy_px ≈ grid spacing.
    - Hungarian (use_hungarian=True, requires scipy): optimal assignment —
      minimises total distance, resolves ambiguous cases where two expected
      holes compete for the same detected hole.  Falls back to greedy when
      scipy is not installed.

    expected_cells: optional (ci, cj) list parallel to expected_points.
      When provided, missing_cells in the returned report contains the cell
      coordinates of each missing expected hole.

    extra = detected holes with no expected match (spurious / reflections).
    max_extra=-1 disables the extra criterion.

    extra_min_dist_factor: if > 0, a detected without a match only counts as
    "extra" if it is more than (extra_min_dist_factor × tol_xy_px) from ANY
    expected position (including already matched ones).  Those that fall closer
    are real holes the phase/tolerance missed — not genuine spurious detections.
    """
    n_exp = len(expected_points)
    n_det = len(detected_points)

    if n_exp == 0:
        return CompareReport(0, n_det, 0, "OK", [], [], extra=n_det,
                             extra_points=list(detected_points))

    if n_det == 0:
        status = "OK" if n_exp <= max_missing else "NOK"
        mc = list(expected_cells) if expected_cells else []
        return CompareReport(n_exp, 0, n_exp, status, list(expected_points), [],
                             extra=0, extra_points=[], missing_cells=mc)

    exp = np.array(expected_points, dtype=np.float32)   # (n_exp, 2)
    det = np.array(detected_points, dtype=np.float32)   # (n_det, 2)

    # Distance matrix — single numpy op, avoids Python loops
    diff  = exp[:, None, :] - det[None, :, :]           # (n_exp, n_det, 2)
    dist2 = (diff * diff).sum(axis=2)                   # (n_exp, n_det)
    tol2  = tol_xy_px * tol_xy_px

    matched_detected_idx: List[int] = []
    missing_points: List[Tuple[float, float]] = []
    missing_exp_idx: List[int] = []

    if use_hungarian and _SCIPY_AVAILABLE:
        # Optimal assignment: minimises total match distance.
        # Pairs with distance > tol are set to a large sentinel so they are
        # only chosen when no valid match exists (those pairs are then treated
        # as unmatched → missing / extra).
        INF = 1e9
        dist_mat = np.sqrt(dist2)                        # (n_exp, n_det)
        cost = np.where(dist_mat <= tol_xy_px, dist_mat, INF)
        row_ind, col_ind = _lsa(cost)                    # type: ignore[misc]

        used_det: set[int] = set()
        matched_exp: set[int] = set()
        for i, j in zip(row_ind, col_ind):
            if cost[i, j] < INF / 2:
                used_det.add(int(j))
                matched_detected_idx.append(int(j))
                matched_exp.add(int(i))

        for i in range(n_exp):
            if i not in matched_exp:
                missing_points.append(expected_points[i])
                missing_exp_idx.append(i)

        used_det_arr = np.zeros(n_det, dtype=bool)
        for j in used_det:
            used_det_arr[j] = True

    else:
        # Greedy closest-first: processes expected points in order of their
        # minimum distance to any detected hole, so near pairs are never stolen.
        used_det_arr = np.zeros(n_det, dtype=bool)
        order = np.argsort(dist2.min(axis=1))

        for i in order:
            row = dist2[i].copy()
            row[used_det_arr] = np.inf
            best_j = int(np.argmin(row))
            if row[best_j] <= tol2:
                used_det_arr[best_j] = True
                matched_detected_idx.append(best_j)
            else:
                missing_points.append(expected_points[i])
                missing_exp_idx.append(int(i))

    missing = len(missing_points)
    raw_extra = [detected_points[j] for j in range(n_det) if not used_det_arr[j]]

    # Filter near-expected extras: holes closer than extra_min_dist_factor×tol to
    # ANY expected position (even already matched ones) are real holes the
    # phase/tolerance missed — not genuine spurious detections (reflections, noise).
    if extra_min_dist_factor > 0.0 and raw_extra:
        min_d2_thr = (tol_xy_px * extra_min_dist_factor) ** 2
        extra_det = np.array(raw_extra, dtype=np.float32)          # (n_raw, 2)
        d2_to_exp = ((extra_det[:, None, :] - exp[None, :, :]) ** 2).sum(axis=2)
        truly_far = d2_to_exp.min(axis=1) > min_d2_thr             # (n_raw,)
        extra_points = [pt for pt, far in zip(raw_extra, truly_far) if far]
    else:
        extra_points = raw_extra

    extra = len(extra_points)

    nok = missing > max_missing or (max_extra >= 0 and extra > max_extra)
    status = "NOK" if nok else "OK"

    missing_cells_out: List[Tuple[int, int]] = (
        [expected_cells[i] for i in missing_exp_idx] if expected_cells else []
    )

    return CompareReport(
        expected=n_exp,
        detected=n_det,
        missing=missing,
        status=status,
        missing_points=missing_points,
        matched_detected_idx=matched_detected_idx,
        extra=extra,
        extra_points=extra_points,
        missing_cells=missing_cells_out,
    )
