import cv2
import numpy as np
from typing import Sequence, Tuple, List

from .detect_holes import Hole


def draw_holes(img_bgr: np.ndarray, holes: Sequence[Hole]) -> np.ndarray:
    out = img_bgr.copy()
    for h in holes:
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 2)
        cv2.circle(out, (int(h.x), int(h.y)), 2, (0, 0, 255), -1)

    cv2.putText(
        out,
        f"Holes: {len(holes)}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return out


def draw_compare_overlay(
    img_bgr: np.ndarray,
    detected: Sequence[Hole],
    missing_points: Sequence[Tuple[float, float]],
    status: str,
) -> np.ndarray:
    out = img_bgr.copy()

    # Detectados: verde
    for h in detected:
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 2)

    # Missing esperados: rojo (marcamos el lugar donde debería estar)
    for (x, y) in missing_points:
        cv2.drawMarker(out, (int(x), int(y)), (0, 0, 255), markerType=cv2.MARKER_TILTED_CROSS, markerSize=25, thickness=3)

    # Texto
    cv2.putText(out, f"STATUS: {status}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(out, f"Missing: {len(missing_points)}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    return out