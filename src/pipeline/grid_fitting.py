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

    # Fractional grid origin from this frame's detected holes
    phase_x_det = estimate_phase(detected_xy[:, 0], dx)
    phase_y_det = estimate_phase(detected_xy[:, 1], dy)

    # Integer cell offset: how many full grid periods the sheet has moved
    if transform is not None:
        tx = float(transform[0, 2])
        ty = float(transform[1, 2])
        k_x = round((phase_ref_x + tx - phase_x_det) / dx)
        k_y = round((phase_ref_y + ty - phase_y_det) / dy)
    else:
        k_x, k_y = 0, 0

    origin_x = phase_x_det + k_x * dx
    origin_y = phase_y_det + k_y * dy

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
