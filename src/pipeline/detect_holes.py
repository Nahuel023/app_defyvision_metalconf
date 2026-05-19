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
    aspect_ratio_max: float | None = None,
    edge_margin_px: float = 0.0,
) -> List[Hole]:
    """
    mask: binaria (agujeros=255). Devuelve lista de agujeros detectados.
    edge_margin_px: descarta agujeros cuyo círculo toca el borde de la imagen
                    (evita medias perforaciones por corte de encuadre).
    """
    if mask.ndim != 2:
        raise ValueError("detect_holes_from_mask espera una máscara 2D.")

    h_img, w_img = mask.shape[:2]
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

        if aspect_ratio_max is not None:
            x, y, w, h = cv2.boundingRect(c)
            if min(w, h) <= 0:
                continue
            aspect_ratio = max(w, h) / min(w, h)
            if aspect_ratio > aspect_ratio_max:
                continue

        # Centroide por momentos: más estable que minEnclosingCircle ante
        # pixels outlier en el borde del contorno.
        M = cv2.moments(c)
        if M["m00"] < 1.0:
            continue
        x = M["m10"] / M["m00"]
        y = M["m01"] / M["m00"]
        _, r = cv2.minEnclosingCircle(c)  # radio sigue desde el círculo envolvente

        if edge_margin_px > 0.0:
            if (x - r < edge_margin_px or
                    y - r < edge_margin_px or
                    x + r > w_img - edge_margin_px or
                    y + r > h_img - edge_margin_px):
                continue

        holes.append(Hole(float(x), float(y), float(r), area, circ))

    # Orden estable (por y y luego x)
    holes.sort(key=lambda h: (h.y, h.x))
    return holes
