import math
from dataclasses import dataclass
from typing import Tuple, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class EdgeAlignResult:
    angle_deg: float
    used_lines: int


def _rotate_keep_size(img_bgr: np.ndarray, angle_deg: float) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    M = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
    return cv2.warpAffine(
        img_bgr, M, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE
    )


def estimate_angle_from_right_edge(
    img_bgr: np.ndarray,
    right_strip_ratio: float = 0.30,
    canny1: int = 50,
    canny2: int = 150,
    hough_threshold: int = 60,
    min_line_len: int = 80,
    max_line_gap: int = 10,
) -> EdgeAlignResult:
    """
    Estima la rotación usando líneas del borde derecho de la chapa.
    Devuelve ángulo (deg) que hay que aplicar para dejar el borde vertical.
    """
    h, w = img_bgr.shape[:2]
    x0 = int(w * (1.0 - right_strip_ratio))
    x0 = max(0, min(x0, w - 1))

    roi = img_bgr[:, x0:w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(gray, canny1, canny2)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=hough_threshold,
        minLineLength=min_line_len,
        maxLineGap=max_line_gap,
    )

    if lines is None or len(lines) == 0:
        return EdgeAlignResult(angle_deg=0.0, used_lines=0)

    # Convertimos líneas a ángulos y nos quedamos con las casi verticales
    vertical_devs = []
    for (x1, y1, x2, y2) in lines[:, 0, :]:
        dx = (x2 - x1)
        dy = (y2 - y1)
        if dx == 0 and dy == 0:
            continue

        ang = math.degrees(math.atan2(dy, dx))  # -180..180
        # Normalizar a 0..180
        if ang < 0:
            ang += 180

        # Vertical ideal = 90°
        dev = ang - 90.0

        # Filtramos: líneas bastante verticales (±30°)
        if abs(dev) <= 30.0:
            # Ponderar por longitud para darle más peso a líneas largas
            length = math.hypot(dx, dy)
            vertical_devs.append((dev, length))

    if len(vertical_devs) == 0:
        return EdgeAlignResult(angle_deg=0.0, used_lines=0)

    # Mediana ponderada simple (repitiendo según longitud aproximada)
    devs = []
    for dev, length in vertical_devs:
        k = max(1, int(length / 50))  # cada 50px "pesa" 1
        devs.extend([dev] * k)

    dev_med = float(np.median(np.array(devs, dtype=float)))

    # Si el borde está inclinado dev_med grados respecto vertical,
    # aplicamos -dev_med para dejarlo vertical.
    angle_to_apply = -dev_med

    return EdgeAlignResult(angle_deg=angle_to_apply, used_lines=len(vertical_devs))


def align_image_by_right_edge(
    img_bgr: np.ndarray,
    max_abs_angle_deg: float = 20.0,
) -> Tuple[np.ndarray, EdgeAlignResult]:
    """
    Alinea la imagen rotando para que el borde derecho quede vertical.
    """
    res = estimate_angle_from_right_edge(img_bgr)
    angle = float(res.angle_deg)

    if abs(angle) > max_abs_angle_deg:
        # por seguridad, no corregimos ángulos gigantes
        return img_bgr, EdgeAlignResult(angle_deg=0.0, used_lines=res.used_lines)

    if abs(angle) < 0.2:  # muy pequeño, no vale la pena
        return img_bgr, EdgeAlignResult(angle_deg=0.0, used_lines=res.used_lines)

    aligned = _rotate_keep_size(img_bgr, angle)
    return aligned, res