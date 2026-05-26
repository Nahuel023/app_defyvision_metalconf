from dataclasses import dataclass, field
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
    extra: int = 0
    extra_points: List[Tuple[float, float]] = field(default_factory=list)


def compare_missing_only(
    expected_points: List[Tuple[float, float]],
    detected_points: List[Tuple[float, float]],
    tol_xy_px: float = 8.0,
    max_missing: int = 0,
    max_extra: int = -1,
    extra_min_dist_factor: float = 0.0,
) -> CompareReport:
    """
    Matching greedy: cada punto esperado toma el detectado más cercano aún libre.
    Usa numpy para calcular la matriz de distancias completa de una sola vez,
    eliminando el loop Python interno O(n*m).

    extra = detectados sin match (contornos espurios / reflejos).
    max_extra=-1 deshabilita el criterio de extra.

    extra_min_dist_factor: si > 0, un detectado sin match solo cuenta como "extra"
    si está a más de (extra_min_dist_factor × tol_xy_px) de CUALQUIER posición
    esperada (incluyendo las ya matcheadas). Los que caen más cerca son agujeros
    reales que el matcher no asignó por error de fase — no espurios genuinos.
    """
    n_exp = len(expected_points)
    n_det = len(detected_points)

    if n_exp == 0:
        return CompareReport(0, n_det, 0, "OK", [], [], extra=n_det,
                             extra_points=list(detected_points))

    if n_det == 0:
        status = "OK" if n_exp <= max_missing else "NOK"
        return CompareReport(n_exp, 0, n_exp, status, list(expected_points), [],
                             extra=0, extra_points=[])

    exp = np.array(expected_points, dtype=np.float32)   # (n_exp, 2)
    det = np.array(detected_points, dtype=np.float32)   # (n_det, 2)

    # Matriz de distancias al cuadrado (n_exp, n_det) — una sola operación numpy
    diff  = exp[:, None, :] - det[None, :, :]           # (n_exp, n_det, 2)
    dist2 = (diff * diff).sum(axis=2)                   # (n_exp, n_det)
    tol2  = tol_xy_px * tol_xy_px

    used_det = np.zeros(n_det, dtype=bool)
    matched_detected_idx: List[int] = []
    missing_points: List[Tuple[float, float]] = []

    # Process expected points closest-first so a near pair is never stolen by a farther one
    order = np.argsort(dist2.min(axis=1))

    for i in order:
        row = dist2[i].copy()
        row[used_det] = np.inf          # enmascarar detectados ya usados
        best_j = int(np.argmin(row))
        if row[best_j] <= tol2:
            used_det[best_j] = True
            matched_detected_idx.append(best_j)
        else:
            missing_points.append(expected_points[i])

    missing = len(missing_points)
    raw_extra = [detected_points[j] for j in range(n_det) if not used_det[j]]

    # Filter near-expected extras: holes closer than extra_min_dist_factor×tol to
    # ANY expected position (even already matched ones) are real holes the
    # phase/tolerance missed — not genuine spurious detections (reflections, noise).
    if extra_min_dist_factor > 0.0 and raw_extra:
        min_d2_thr = (tol_xy_px * extra_min_dist_factor) ** 2
        extra_det = np.array(raw_extra, dtype=np.float32)          # (n_raw, 2)
        # (n_raw, n_exp) squared distances to every expected position
        d2_to_exp = ((extra_det[:, None, :] - exp[None, :, :]) ** 2).sum(axis=2)
        truly_far = d2_to_exp.min(axis=1) > min_d2_thr             # (n_raw,)
        extra_points = [pt for pt, far in zip(raw_extra, truly_far) if far]
    else:
        extra_points = raw_extra

    extra = len(extra_points)

    nok = missing > max_missing or (max_extra >= 0 and extra > max_extra)
    status = "NOK" if nok else "OK"

    return CompareReport(
        expected=n_exp,
        detected=n_det,
        missing=missing,
        status=status,
        missing_points=missing_points,
        matched_detected_idx=matched_detected_idx,
        extra=extra,
        extra_points=extra_points,
    )
