from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


@dataclass(frozen=True)
class CompareReport:
    expected: int
    detected: int
    missing: int
    status: str  # "OK" o "NOK"
    missing_points: List[Tuple[float, float]]
    matched_detected_idx: List[int]  # índices de detectados que matchearon (para overlay)


def compare_missing_only(
    expected_points: List[Tuple[float, float]],
    detected_points: List[Tuple[float, float]],
    tol_xy_px: float = 8.0,
    max_missing: int = 0,
) -> CompareReport:
    """
    Matching greedy: cada punto esperado toma el detectado más cercano aún libre.
    Usa numpy para calcular la matriz de distancias completa de una sola vez,
    eliminando el loop Python interno O(n*m).
    """
    n_exp = len(expected_points)
    n_det = len(detected_points)

    if n_exp == 0:
        return CompareReport(0, n_det, 0, "OK", [], [])

    if n_det == 0:
        status = "OK" if n_exp <= max_missing else "NOK"
        return CompareReport(n_exp, 0, n_exp, status, list(expected_points), [])

    exp = np.array(expected_points, dtype=np.float32)   # (n_exp, 2)
    det = np.array(detected_points, dtype=np.float32)   # (n_det, 2)

    # Matriz de distancias al cuadrado (n_exp, n_det) — una sola operación numpy
    diff  = exp[:, None, :] - det[None, :, :]           # (n_exp, n_det, 2)
    dist2 = (diff * diff).sum(axis=2)                   # (n_exp, n_det)
    tol2  = tol_xy_px * tol_xy_px

    used_det = np.zeros(n_det, dtype=bool)
    matched_detected_idx: List[int] = []
    missing_points: List[Tuple[float, float]] = []

    for i in range(n_exp):
        row = dist2[i].copy()
        row[used_det] = np.inf          # enmascarar detectados ya usados
        best_j = int(np.argmin(row))
        if row[best_j] <= tol2:
            used_det[best_j] = True
            matched_detected_idx.append(best_j)
        else:
            missing_points.append(expected_points[i])

    missing = len(missing_points)
    status = "OK" if missing <= max_missing else "NOK"

    return CompareReport(
        expected=n_exp,
        detected=n_det,
        missing=missing,
        status=status,
        missing_points=missing_points,
        matched_detected_idx=matched_detected_idx,
    )
