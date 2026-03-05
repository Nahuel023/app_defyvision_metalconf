import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Hole:
    x: float
    y: float
    r: float
    area: float
    circularity: float


def _circularity(area: float, perimeter: float) -> float:
    if perimeter <= 0:
        return 0.0
    return float(4.0 * np.pi * area / (perimeter * perimeter))


def detect_holes_from_mask(
    mask: np.ndarray,
    min_area: float = 80.0,
    max_area: float | None = None,
    circularity_min: float = 0.6,
) -> List[Hole]:
    """
    mask: binaria (agujeros=255). Devuelve lista de agujeros detectados.
    """
    if mask.ndim != 2:
        raise ValueError("detect_holes_from_mask espera una máscara 2D.")

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    holes: List[Hole] = []
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < min_area:
            continue
        if max_area is not None and area > max_area:
            continue

        per = float(cv2.arcLength(c, True))
        circ = _circularity(area, per)
        if circ < circularity_min:
            continue

        (x, y), r = cv2.minEnclosingCircle(c)
        holes.append(Hole(float(x), float(y), float(r), area, circ))

    # Orden estable (por y y luego x)
    holes.sort(key=lambda h: (h.y, h.x))
    return holes