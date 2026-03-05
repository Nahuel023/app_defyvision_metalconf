from dataclasses import dataclass
from typing import List, Tuple
import math


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
) -> CompareReport:
    """
    Matching simple: cada punto esperado busca el detectado más cercano que aún no se usó.
    Si no hay ninguno dentro de tol_xy_px => missing.
    """
    used = set()
    matched_detected_idx: List[int] = []
    missing_points: List[Tuple[float, float]] = []

    for (ex, ey) in expected_points:
        best_i = -1
        best_d = 1e18
        for i, (dx, dy) in enumerate(detected_points):
            if i in used:
                continue
            d = (dx - ex) * (dx - ex) + (dy - ey) * (dy - ey)
            if d < best_d:
                best_d = d
                best_i = i

        if best_i == -1:
            missing_points.append((ex, ey))
            continue

        if math.sqrt(best_d) <= tol_xy_px:
            used.add(best_i)
            matched_detected_idx.append(best_i)
        else:
            missing_points.append((ex, ey))

    missing = len(missing_points)
    status = "OK" if missing == 0 else "NOK"

    return CompareReport(
        expected=len(expected_points),
        detected=len(detected_points),
        missing=missing,
        status=status,
        missing_points=missing_points,
        matched_detected_idx=matched_detected_idx,
    )