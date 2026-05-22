from __future__ import annotations

import cv2
import numpy as np
from typing import TYPE_CHECKING, Sequence, Tuple, List

from .detect_holes import Hole

if TYPE_CHECKING:
    from .edge_centering import CenteringResult


def _draw_transparent_line(
    img: np.ndarray,
    pt1: tuple,
    pt2: tuple,
    color: tuple,
    thickness: int = 2,
    alpha: float = 0.45,
) -> None:
    """Draw a line onto img in-place with the given opacity (alpha 0=invisible, 1=solid)."""
    layer = np.zeros_like(img)
    cv2.line(layer, pt1, pt2, color, thickness)
    mask = layer.any(axis=2)
    img[mask] = np.clip(
        img[mask].astype(np.float32) * (1.0 - alpha) + layer[mask].astype(np.float32) * alpha,
        0, 255,
    ).astype(np.uint8)


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
    extra_points: Sequence[Tuple[float, float]] = (),
    near_miss_pairs: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]] = (),
) -> np.ndarray:
    """Draw inspection overlay.

    near_miss_pairs: list of ((exp_x, exp_y), (det_x, det_y)) for expected points
    that have a detected hole nearby but outside tol_xy_px. A thin cyan line
    connects them so the operator can see the gap at a glance.
    """
    out = img_bgr.copy()

    # Líneas near-miss: esperado → detectado más cercano (fuera de tolerancia)
    for (ex, ey), (dx, dy) in near_miss_pairs:
        cv2.line(out, (int(ex), int(ey)), (int(dx), int(dy)),
                 (255, 220, 0), 1, cv2.LINE_AA)

    # Detectados: verde
    for h in detected:
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 2)

    # Missing esperados: rojo
    # cruz roja = posición esperada sin match dentro de tol_xy_px
    for (x, y) in missing_points:
        cv2.drawMarker(out, (int(x), int(y)), (0, 0, 255),
                       markerType=cv2.MARKER_TILTED_CROSS, markerSize=25, thickness=3)

    # Extra detectados (espurios): naranja diamante
    # diamante naranja = detectado sin posición esperada asignada
    for (x, y) in extra_points:
        cv2.drawMarker(out, (int(x), int(y)), (0, 165, 255),
                       markerType=cv2.MARKER_DIAMOND, markerSize=20, thickness=2)

    # Color de texto según estado
    if status == "OK":
        status_color = (0, 220, 0)
    elif status == "NOK":
        status_color = (0, 0, 255)
    else:
        status_color = (0, 200, 255)

    cv2.putText(out, f"STATUS: {status}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, status_color, 2, cv2.LINE_AA)
    cv2.putText(out, f"Missing: {len(missing_points)}", (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    if extra_points:
        cv2.putText(out, f"Extra: {len(extra_points)}", (10, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 165, 255), 2, cv2.LINE_AA)

    return out


def draw_centering_overlay(
    img_bgr: np.ndarray,
    centering: "CenteringResult",
    tag_nok: bool = False,
) -> np.ndarray:
    """Draw metal edge lines, pattern bound lines, center lines, and margin annotation.

    tag_nok: when True, draws a prominent DESCENTRADO badge on the frame.
    """
    out = img_bgr.copy()
    h, w = out.shape[:2]

    lx  = int(round(centering.left_x))
    rx  = int(round(centering.right_x))
    cx  = int(round(centering.sheet_center_x))
    hx  = int(round(centering.holes_center_x))
    plx = int(round(centering.pattern_left_x))
    prx = int(round(centering.pattern_right_x))

    # Metal edges: light gray semi-transparent vertical lines
    _draw_transparent_line(out, (lx, 0), (lx, h - 1), (210, 210, 210), thickness=2, alpha=0.45)
    _draw_transparent_line(out, (rx, 0), (rx, h - 1), (210, 210, 210), thickness=2, alpha=0.45)

    # Pattern bounds: thin yellow dashed lines (izquierdo y derecho del patrón detectado)
    _pat_color = (50, 220, 255)   # amarillo-cyan
    for y in range(0, h, 20):
        cv2.line(out, (plx, y), (plx, min(y + 12, h - 1)), _pat_color, 1)
        cv2.line(out, (prx, y), (prx, min(y + 12, h - 1)), _pat_color, 1)

    # Sheet center: orange dashed line
    for y in range(0, h, 20):
        cv2.line(out, (cx, y), (cx, min(y + 10, h - 1)), (0, 165, 255), 2)

    # Holes center: white line
    cv2.line(out, (hx, 0), (hx, h - 1), (255, 255, 255), 1)

    # Offset arrow from sheet center to holes center
    color = (0, 200, 0) if centering.within_tol else (0, 0, 255)
    mid_y = h // 2
    if abs(hx - cx) >= 3:
        cv2.arrowedLine(out, (cx, mid_y), (hx, mid_y), color, 3, tipLength=0.3)
    else:
        cv2.circle(out, (cx, mid_y), 6, (0, 200, 0), -1)

    # Text: left margin, right margin, offset
    sign = "+" if centering.offset_px >= 0 else ""
    lm = centering.left_margin_px
    rm = centering.right_margin_px
    cv2.putText(out, f"Izq: {lm:.0f}px  Der: {rm:.0f}px",
                (10, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1, cv2.LINE_AA)
    cv2.putText(out, f"Offset: {sign}{centering.offset_px:.1f}px",
                (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)

    # Prominent DESCENTRADO badge when centering triggered NOK
    if tag_nok:
        label = "NOK CENTRADO"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thick = 1.1, 3
        (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
        bx1, by1 = w // 2 - tw // 2 - 8, 105
        bx2, by2 = w // 2 + tw // 2 + 8, 105 + th + baseline + 10
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 180), -1)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 255), 2)
        cv2.putText(out, label, (bx1 + 8, by2 - baseline - 4),
                    font, scale, (255, 255, 255), thick, cv2.LINE_AA)

    return out
