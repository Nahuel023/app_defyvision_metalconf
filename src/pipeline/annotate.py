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


def _draw_edge_polyline(
    img: np.ndarray,
    points: Sequence[Tuple[float, float]],
    color: tuple,
    thickness: int = 2,
    alpha: float = 0.55,
    dot_radius: int = 3,
) -> None:
    """Draw a polyline through real per-band edge points with sample dots.

    All segments are drawn onto a single off-screen layer which is then blended
    in one pass — avoids N separate alpha compositing operations per polyline.
    """
    if len(points) < 1:
        return
    sorted_pts = sorted(points, key=lambda p: p[1])

    # Single layer for all segments + dots, one alpha blend
    layer = np.zeros_like(img)
    for j in range(len(sorted_pts) - 1):
        x0, y0 = int(round(sorted_pts[j][0])),     int(round(sorted_pts[j][1]))
        x1, y1 = int(round(sorted_pts[j + 1][0])), int(round(sorted_pts[j + 1][1]))
        cv2.line(layer, (x0, y0), (x1, y1), color, thickness)
    for (x, y) in sorted_pts:
        cv2.circle(layer, (int(round(x)), int(round(y))), dot_radius, color, -1)

    mask = layer.any(axis=2)
    if mask.any():
        img[mask] = np.clip(
            img[mask].astype(np.float32) * (1.0 - alpha)
            + layer[mask].astype(np.float32) * alpha,
            0, 255,
        ).astype(np.uint8)


def draw_centering_overlay(
    img_bgr: np.ndarray,
    centering: "CenteringResult",
    tag_nok: bool = False,
    roi_x: int = 0,
    roi_y: int = 0,
) -> np.ndarray:
    """Draw metal edge lines, pattern bound lines, center lines, and margin annotation.

    roi_x / roi_y: offset to convert ROI-relative coordinates to full-frame coordinates.
    When drawing on a full-frame image (not the ROI crop), pass roi.x and roi.y so that
    CHAPA edge lines land on the real sheet edges (which are outside the ROI crop).
    When roi_x=roi_y=0 (default), behaviour is unchanged from the original.

    Uses real per-band edge points when available (polyline + dots).
    Falls back to single vertical lines when band data is absent.
    tag_nok: when True, draws a prominent DESCENTRADO badge on the frame.
    """
    out = img_bgr.copy()
    h, w = out.shape[:2]

    cx  = int(round(centering.sheet_center_x  + roi_x))
    hx  = int(round(centering.holes_center_x  + roi_x))

    # Scalar fallback positions (used when per-band data is unavailable)
    lx  = int(round(centering.left_x          + roi_x))
    rx  = int(round(centering.right_x         + roi_x))
    plx = int(round(centering.pattern_left_x  + roi_x))
    prx = int(round(centering.pattern_right_x + roi_x))

    # Offset per-band point lists to full-frame space
    _off = (roi_x, roi_y)
    _shift = lambda pts: tuple((x + _off[0], y + _off[1]) for x, y in pts)  # noqa: E731
    left_pts  = _shift(getattr(centering, "left_edge_points",   ()))
    right_pts = _shift(getattr(centering, "right_edge_points",  ()))
    pat_l_pts = _shift(getattr(centering, "pattern_left_points",  ()))
    pat_r_pts = _shift(getattr(centering, "pattern_right_points", ()))

    _font_sm = cv2.FONT_HERSHEY_SIMPLEX

    # --- Metal edges (CHAPA): real polyline or fallback vertical line ---
    _edge_color = (210, 210, 210)
    # Clamp x to image bounds for label placement (edge may be outside ROI)
    lx_vis = max(3, min(lx, w - 55))
    rx_vis = max(3, min(rx, w - 55))
    if len(left_pts) >= 2:
        _draw_edge_polyline(out, left_pts, _edge_color, thickness=2, alpha=0.50)
    else:
        _draw_transparent_line(out, (lx, 0), (lx, h - 1), _edge_color, 2, 0.45)
    cv2.putText(out, "CHAPA", (lx_vis, 28), _font_sm, 0.45, _edge_color, 1, cv2.LINE_AA)

    if len(right_pts) >= 2:
        _draw_edge_polyline(out, right_pts, _edge_color, thickness=2, alpha=0.50)
    else:
        _draw_transparent_line(out, (rx, 0), (rx, h - 1), _edge_color, 2, 0.45)
    cv2.putText(out, "CHAPA", (rx_vis, 28), _font_sm, 0.45, _edge_color, 1, cv2.LINE_AA)

    # --- Pattern bounds (PATRON): real polyline or fallback dashed line ---
    _pat_color = (50, 220, 255)   # amarillo-cyan
    plx_vis = max(3, min(plx, w - 65))
    prx_vis = max(3, min(prx, w - 65))
    if len(pat_l_pts) >= 2:
        _draw_edge_polyline(out, pat_l_pts, _pat_color, thickness=1, alpha=0.65, dot_radius=2)
    else:
        for y in range(0, h, 20):
            cv2.line(out, (plx, y), (plx, min(y + 12, h - 1)), _pat_color, 1)
    cv2.putText(out, "PATRON", (plx_vis, 44), _font_sm, 0.45, _pat_color, 1, cv2.LINE_AA)

    if len(pat_r_pts) >= 2:
        _draw_edge_polyline(out, pat_r_pts, _pat_color, thickness=1, alpha=0.65, dot_radius=2)
    else:
        for y in range(0, h, 20):
            cv2.line(out, (prx, y), (prx, min(y + 12, h - 1)), _pat_color, 1)
    cv2.putText(out, "PATRON", (prx_vis, 44), _font_sm, 0.45, _pat_color, 1, cv2.LINE_AA)

    # --- Sheet center: orange dashed line ---
    for y in range(0, h, 20):
        cv2.line(out, (cx, y), (cx, min(y + 10, h - 1)), (0, 165, 255), 2)

    # --- Holes center: white line ---
    cv2.line(out, (hx, 0), (hx, h - 1), (255, 255, 255), 1)

    # --- Offset arrow from sheet center to holes center ---
    color = (0, 200, 0) if centering.within_tol else (0, 0, 255)
    mid_y = h // 2
    if abs(hx - cx) >= 3:
        cv2.arrowedLine(out, (cx, mid_y), (hx, mid_y), color, 3, tipLength=0.3)
    else:
        cv2.circle(out, (cx, mid_y), 6, (0, 200, 0), -1)

    # --- Text: margins, delta, offset, verticality ---
    lm = centering.left_margin_px
    rm = centering.right_margin_px
    delta = centering.margin_delta_px
    offset = centering.offset_px
    sign_d = "+" if delta >= 0 else ""
    sign_o = "+" if offset >= 0 else ""

    text_y_base = h - 15
    # Row 1 (bottom): margins left / right
    cv2.putText(out, f"Izq: {lm:.0f}px   Der: {rm:.0f}px",
                (10, text_y_base), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1, cv2.LINE_AA)
    # Row 2: delta + offset
    cv2.putText(out, f"Delta: {sign_d}{delta:.1f}px   Offset: {sign_o}{offset:.1f}px",
                (10, text_y_base - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    # Row 3: pattern edge verticality
    pl_slope = getattr(centering, "pattern_left_slope_deg", 0.0)
    pr_slope = getattr(centering, "pattern_right_slope_deg", 0.0)
    vert_color = (80, 200, 255)
    cv2.putText(out, f"Vert pat: Izq={pl_slope:+.1f}\xb0  Der={pr_slope:+.1f}\xb0",
                (10, text_y_base - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, vert_color, 1, cv2.LINE_AA)

    # --- "CENTRADO NO CONFIABLE" badge when too few bands detected ---
    centering_reliable = getattr(centering, "centering_reliable", True)
    if not centering_reliable:
        label_nc = "BORDES NO CONFIABLES"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale_nc, thick_nc = 0.75, 2
        (tw, th), bl = cv2.getTextSize(label_nc, font, scale_nc, thick_nc)
        bx1, by1 = w // 2 - tw // 2 - 8, 140
        bx2, by2 = w // 2 + tw // 2 + 8, 140 + th + bl + 8
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 100, 180), -1)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 165, 255), 2)
        cv2.putText(out, label_nc, (bx1 + 8, by2 - bl - 4),
                    font, scale_nc, (255, 255, 255), thick_nc, cv2.LINE_AA)

    # --- Prominent DESCENTRADO badge when centering triggered NOK ---
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
