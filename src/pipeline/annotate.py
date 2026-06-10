from __future__ import annotations

import cv2
import numpy as np
from typing import TYPE_CHECKING, Sequence, Tuple, List

from .detect_holes import Hole
from .edge_centering import _fit_line_robust, _line_x_at_y

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


# Estimated pixel height of one machine-stop badge (used to offset the NOK panel).
# Derived from draw_machine_stop_badge geometry: ~86px banner + 3px gap = 89px per badge.
_BADGE_H = 92


def _draw_nok_reasons_panel(
    img: np.ndarray,
    reasons: list[str],
    y_start: int = 0,
) -> None:
    """Draw a semi-transparent NOK reasons panel anchored at y_start in the top-left corner.

    y_start: top Y of the panel (pixels from top of image).  Pass badge_count * _BADGE_H
    to keep the panel below any machine-stop badges that will be drawn on the same image.
    Each reason is listed as a bullet line.  Panel height adapts to the number of reasons.
    Drawn in-place on `img`.
    """
    if not reasons:
        return

    font       = cv2.FONT_HERSHEY_SIMPLEX
    hdr_scale  = 0.60
    hdr_thick  = 2
    row_scale  = 0.45
    row_thick  = 1
    pad        = 8
    row_gap    = 4

    header = "  NOK"
    (hw, hh), hbl = cv2.getTextSize(header, font, hdr_scale, hdr_thick)

    row_sizes = [cv2.getTextSize(f"  {r}", font, row_scale, row_thick) for r in reasons]
    row_h_max = max((s[0][1] + s[1]) for s in row_sizes) if row_sizes else 0

    panel_w = max(hw, max(s[0][0] for s in row_sizes)) + pad * 2
    panel_h = (pad + hh + hbl + pad // 2
               + len(reasons) * (row_h_max + row_gap)
               + pad)

    y0, y1 = y_start, y_start + panel_h

    # Semi-transparent dark-red background
    layer = img.copy()
    cv2.rectangle(layer, (0, y0), (panel_w, y1), (0, 0, 100), -1)
    cv2.addWeighted(layer, 0.80, img, 0.20, 0, img)

    # Red border (bottom + right)
    cv2.line(img, (0, y1), (panel_w, y1), (0, 0, 220), 2)
    cv2.line(img, (panel_w, y0), (panel_w, y1), (0, 0, 220), 2)

    # Header "NOK"
    y = y0 + pad + hh
    cv2.putText(img, header, (pad, y), font, hdr_scale, (50, 50, 255), hdr_thick + 2, cv2.LINE_AA)
    cv2.putText(img, header, (pad, y), font, hdr_scale, (255, 255, 255), hdr_thick, cv2.LINE_AA)

    # Reason rows
    y += hbl + pad // 2
    for r in reasons:
        (_, rh), rbl = cv2.getTextSize(f"  {r}", font, row_scale, row_thick)
        y += rh
        cv2.putText(img, f"  {r}", (pad, y), font, row_scale, (0, 180, 255), row_thick, cv2.LINE_AA)
        y += rbl + row_gap


def draw_compare_overlay(
    img_bgr: np.ndarray,
    detected: Sequence[Hole],
    missing_points: Sequence[Tuple[float, float]],
    status: str,
    raw_detected: Sequence[Hole] = (),
    extra_points: Sequence[Tuple[float, float]] = (),
    near_miss_pairs: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]] = (),
    nok_reasons: List[str] = (),
    nok_panel_badge_count: int = 0,
    draw_status: bool = True,
) -> np.ndarray:
    """Draw inspection overlay.

    near_miss_pairs: list of ((exp_x, exp_y), (det_x, det_y)) for expected points
    that have a detected hole nearby but outside tol_xy_px. A thin cyan line
    connects them so the operator can see the gap at a glance.
    nok_reasons: list of human-readable cause strings drawn as a panel when NOK.
    nok_panel_badge_count: number of top machine-stop/desalignment banners that
    will be drawn later on the full frame. Used to push the NOK panel below them.
    draw_status: when False, the OK/NOK status indicator is NOT drawn here. The
    caller is expected to draw it later via draw_status_indicator() on the full
    frame (so it lands on the left edge, not over the holes in the ROI).
    """
    out = img_bgr.copy()

    # Líneas near-miss: esperado → detectado más cercano (fuera de tolerancia)
    for (ex, ey), (dx, dy) in near_miss_pairs:
        cv2.line(out, (int(ex), int(ey)), (int(dx), int(dy)),
                 (255, 220, 0), 1, cv2.LINE_AA)

    # Detectados crudos fuera del overlay comparativo: cian tenue.
    # Permite distinguir "no detectado" de "detectado pero descartado por
    # filtros del patron/bbox/hull", evitando diagnosticos engañosos.
    detected_keys = {
        (round(float(h.x), 2), round(float(h.y), 2), round(float(h.r), 2))
        for h in detected
    }
    for h in raw_detected:
        key = (round(float(h.x), 2), round(float(h.y), 2), round(float(h.r), 2))
        if key in detected_keys:
            continue
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (255, 255, 0), 1)

    # Detectados usados en la comparación: verde
    for h in detected:
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 2)

    # Missing esperados: marcador hueco — sin relleno para ver la imagen debajo
    for i, (x, y) in enumerate(missing_points):
        ix, iy = int(x), int(y)
        # Círculo hueco: indica la posición esperada sin tapar el agujero debajo
        cv2.circle(out, (ix, iy), 10, (0, 0, 0),   2)   # sombra negra
        cv2.circle(out, (ix, iy), 10, (0, 0, 255),  2)   # borde rojo
        # Cruz: sombra primero, luego roja
        cv2.drawMarker(out, (ix, iy), (0, 0, 0),
                       markerType=cv2.MARKER_TILTED_CROSS, markerSize=16, thickness=3)
        cv2.drawMarker(out, (ix, iy), (0, 0, 255),
                       markerType=cv2.MARKER_TILTED_CROSS, markerSize=16, thickness=2)
        # Número del faltante
        label = str(i + 1)
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        cv2.putText(out, label, (ix - lw // 2, iy + lh // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0),     2, cv2.LINE_AA)
        cv2.putText(out, label, (ix - lw // 2, iy + lh // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

    # Extra detectados (espurios): naranja diamante
    for (x, y) in extra_points:
        cv2.drawMarker(out, (int(x), int(y)), (0, 165, 255),
                       markerType=cv2.MARKER_DIAMOND, markerSize=12, thickness=2)

    # Indicador de estado (OK/NOK). Por defecto aquí; con draw_status=False el
    # caller lo dibuja sobre el frame completo (borde izquierdo) vía draw_status_indicator.
    if draw_status:
        draw_status_indicator(out, status, nok_reasons, nok_panel_badge_count)

    return out


def draw_status_indicator(
    img: np.ndarray,
    status: str,
    nok_reasons: List[str] = (),
    badge_count: int = 0,
) -> np.ndarray:
    """Dibuja el estado OK/NOK pegado al BORDE IZQUIERDO de la imagen.

    Pensado para llamarse sobre el frame COMPLETO (no sobre el recorte ROI), de modo
    que el texto/panel quede en la zona oscura de la izquierda y no tape los agujeros.
    NOK → panel de causas; OK → texto compacto. Dibuja in-place y devuelve img.
    """
    if status == "NOK" and nok_reasons:
        y_start = max(0, int(badge_count) * _BADGE_H)
        _draw_nok_reasons_panel(img, list(nok_reasons), y_start=y_start)
    else:
        color = (0, 220, 0) if status == "OK" else (0, 200, 255)
        cv2.putText(img, f"STATUS: {status}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    return img


def draw_tilt_indicator(
    img: np.ndarray,
    tilt_deg: float,
    warn: bool = False,
    y: int = 62,
) -> np.ndarray:
    """Muestra la inclinación de la grilla al borde izquierdo, bajo el STATUS.

    Texto normal (cian) cuando está dentro de lo esperado; amarillo/rojo + badge
    "CHAPA INCLINADA" cuando `warn` es True. NaN → no dibuja nada. In-place.
    """
    import math
    if tilt_deg is None or (isinstance(tilt_deg, float) and math.isnan(tilt_deg)):
        return img
    color = (0, 0, 255) if warn else (200, 200, 80)
    cv2.putText(img, f"Inclinacion: {tilt_deg:+.1f} grados", (10, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, f"Inclinacion: {tilt_deg:+.1f} grados", (10, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    if warn:
        label = "! CHAPA INCLINADA"
        (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        bx, by = 10, y + 8
        cv2.rectangle(img, (bx, by), (bx + tw + 10, by + th + bl + 8), (0, 0, 150), -1)
        cv2.rectangle(img, (bx, by), (bx + tw + 10, by + th + bl + 8), (0, 0, 255), 2)
        cv2.putText(img, label, (bx + 5, by + th + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return img


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


def _draw_full_height_fit_line(
    img: np.ndarray,
    points: Sequence[Tuple[float, float]],
    color: tuple,
    thickness: int = 1,
    alpha: float = 0.75,
) -> None:
    """Draw the fitted edge line across the full frame height for visual audit."""
    if len(points) < 2:
        return
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    try:
        a, b = np.polyfit(ys, xs, 1)
    except Exception:
        return
    h, w = img.shape[:2]
    x0 = int(round(b))
    x1 = int(round(a * (h - 1) + b))
    x0 = max(-w, min(2 * w, x0))
    x1 = max(-w, min(2 * w, x1))
    _draw_transparent_line(img, (x0, 0), (x1, h - 1), color, thickness, alpha)


def draw_blur_indicator(
    img: np.ndarray,
    blur_score: float,
    blur_score_min: float,
    y: int = 82,
) -> np.ndarray:
    """Muestra la nitidez Laplaciana bajo el indicador de inclinación.

    Verde cuando el score supera blur_score_min (imagen nítida).
    Rojo + badge cuando está por debajo (imagen borrosa / LOW_QUALITY).
    """
    if blur_score_min <= 0:
        return img
    import math
    is_blurry = blur_score < blur_score_min
    color = (0, 0, 220) if is_blurry else (80, 200, 80)
    label = f"Nitidez: {blur_score:.0f}"
    cv2.putText(img, label, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, label, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    if is_blurry:
        badge = "! IMAGEN BORROSA"
        (tw, th), bl = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        bx, by = 10, y + 8
        cv2.rectangle(img, (bx, by), (bx + tw + 10, by + th + bl + 8), (0, 0, 150), -1)
        cv2.rectangle(img, (bx, by), (bx + tw + 10, by + th + bl + 8), (0, 0, 255), 2)
        cv2.putText(img, badge, (bx + 5, by + th + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def draw_roi_indicator(
    img: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    label: str = "ROI",
) -> np.ndarray:
    """Draw the analysis ROI on the full-frame overlay."""
    out = img.copy()
    x1, y1 = max(0, int(x)), max(0, int(y))
    x2, y2 = min(out.shape[1] - 1, int(x + w)), min(out.shape[0] - 1, int(y + h))
    if x1 >= x2 or y1 >= y2:
        return out

    layer = out.copy()
    cv2.rectangle(layer, (x1, y1), (x2, y2), (0, 255, 255), -1)
    cv2.addWeighted(layer, 0.08, out, 0.92, 0, out)
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 2)

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.55, 2
    (tw, th), bl = cv2.getTextSize(label, font, scale, thick)
    by2 = min(out.shape[0] - 1, y1 + th + bl + 8)
    bx2 = min(out.shape[1] - 1, x1 + tw + 14)
    cv2.rectangle(out, (x1, y1), (bx2, by2), (0, 120, 120), -1)
    cv2.rectangle(out, (x1, y1), (bx2, by2), (0, 255, 255), 1)
    cv2.putText(out, label, (x1 + 7, y1 + th + 3), font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return out


def draw_machine_stop_badge(
    img: np.ndarray,
    reason: str = "",
    index: int = 0,
    title: str = "! DETENCION DE MAQUINA",
) -> np.ndarray:
    """Draw compact strip at the top of the image.

    index: stacking order — 0 = topmost strip, 1 = immediately below the first.
    Positioned at the very top so it never covers the hole/pattern area.
    title: header text shown in the banner (default: DETENCION DE MAQUINA).
    """
    out = img.copy()
    h, w = out.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX

    main_label = title
    main_scale, main_thick = 0.80, 2
    (mw, mh), mbl = cv2.getTextSize(main_label, font, main_scale, main_thick)

    reason_scale, reason_thick = 0.48, 1
    (rw, rh), rbl = (cv2.getTextSize(reason, font, reason_scale, reason_thick)
                     if reason else ((0, 0), 0))

    pad_v = 9
    banner_h = pad_v + mh + mbl + (rh + rbl + 6 if reason else 0) + pad_v
    banner_y = index * (banner_h + 3)

    # Semi-transparent dark-red background
    layer = out.copy()
    cv2.rectangle(layer, (0, banner_y), (w, banner_y + banner_h), (0, 0, 130), -1)
    cv2.addWeighted(layer, 0.88, out, 0.12, 0, out)

    # Red border bottom; top border only when stacked (not at the very edge)
    cv2.line(out, (0, banner_y + banner_h), (w, banner_y + banner_h), (0, 0, 255), 3)
    if banner_y > 0:
        cv2.line(out, (0, banner_y), (w, banner_y), (0, 0, 180), 2)

    # Main text — centered, white with dark shadow
    mx = w // 2 - mw // 2
    my = banner_y + pad_v + mh
    cv2.putText(out, main_label, (mx + 2, my + 2), font, main_scale,
                (0, 0, 60), main_thick + 3, cv2.LINE_AA)
    cv2.putText(out, main_label, (mx, my), font, main_scale,
                (255, 255, 255), main_thick, cv2.LINE_AA)

    # Reason text — amber/yellow, centered below
    if reason:
        ry_pos = my + mbl + rh + 6
        rx = w // 2 - rw // 2
        cv2.putText(out, reason, (rx + 1, ry_pos + 1), font, reason_scale,
                    (0, 0, 60), reason_thick + 1, cv2.LINE_AA)
        cv2.putText(out, reason, (rx, ry_pos), font, reason_scale,
                    (0, 210, 255), reason_thick, cv2.LINE_AA)

    return out


def draw_centering_overlay(
    img_bgr: np.ndarray,
    centering: "CenteringResult",
    tag_nok: bool = False,
    roi_x: int = 0,
    roi_y: int = 0,
    pattern_warn: bool = False,
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
    left_pts        = _shift(getattr(centering, "left_edge_points",     ()))
    right_pts       = _shift(getattr(centering, "right_edge_points",    ()))
    pat_l_pts       = _shift(getattr(centering, "pattern_left_points",  ()))
    pat_r_pts       = _shift(getattr(centering, "pattern_right_points", ()))
    sheet_ctr_pts   = _shift(getattr(centering, "sheet_center_points",  ()))
    pat_ctr_pts     = _shift(getattr(centering, "pattern_center_points", ()))

    _font_sm = cv2.FONT_HERSHEY_SIMPLEX

    # --- Metal edges (CHAPA): actual per-band measurements as polyline ---
    _edge_color = (210, 210, 210)
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
    # When pattern_warn: draw in orange-red with glow + circle at worst deviation point.
    _pat_color      = (50, 220, 255)   # normal: cyan
    _pat_warn_color = (0, 110, 255)    # warning: vivid orange (BGR)
    pat_color  = _pat_warn_color if pattern_warn else _pat_color
    pat_thick  = 2 if pattern_warn else 1
    pat_alpha  = 0.90 if pattern_warn else 0.65
    pat_label  = "PATRON !!" if pattern_warn else "PATRON"
    pat_lw     = 2 if pattern_warn else 1

    plx_vis = max(3, min(plx, w - 65))
    prx_vis = max(3, min(prx, w - 65))

    for pts, fallback_x in ((pat_l_pts, plx), (pat_r_pts, prx)):
        x_vis = max(3, min(fallback_x, w - 65))
        if len(pts) >= 2:
            if pattern_warn:
                # Glow layer: thick dark red beneath the bright line
                _draw_edge_polyline(out, pts, (0, 0, 80),
                                    thickness=pat_thick + 4, alpha=0.65, dot_radius=0)
            _draw_edge_polyline(out, pts, pat_color,
                                thickness=pat_thick, alpha=pat_alpha, dot_radius=3)
            # Mark worst-deviation point with circle + exclamation
            if pattern_warn and len(pts) >= 3:
                xs = [p[0] for p in pts]
                mean_x = sum(xs) / len(xs)
                wi = max(range(len(pts)), key=lambda i: abs(pts[i][0] - mean_x))
                wp = (int(round(pts[wi][0])), int(round(pts[wi][1])))
                cv2.circle(out, wp, 20, (0, 0, 200), 4)
                cv2.circle(out, wp, 22, (255, 255, 255), 1)
                cv2.putText(out, "!", (wp[0] - 7, wp[1] + 9),
                            _font_sm, 0.9, (0, 0, 255), 3, cv2.LINE_AA)
        cv2.putText(out, pat_label, (x_vis, 44), _font_sm, 0.45, pat_color, pat_lw, cv2.LINE_AA)

    # --- Sheet center: real per-band polyline + fitted extension, or dashed fallback ---
    _shc_color = (0, 165, 255)   # orange
    if len(sheet_ctr_pts) >= 2:
        _draw_full_height_fit_line(out, sheet_ctr_pts, _shc_color, thickness=1, alpha=0.30)
        _draw_edge_polyline(out, sheet_ctr_pts, _shc_color, thickness=2, alpha=0.80, dot_radius=0)
    else:
        for y in range(0, h, 20):
            cv2.line(out, (cx, y), (cx, min(y + 10, h - 1)), _shc_color, 2)

    # --- Pattern center: real per-band polyline + fitted extension, or single line fallback ---
    _ptc_color = (255, 255, 255)   # white
    if len(pat_ctr_pts) >= 2:
        _draw_full_height_fit_line(out, pat_ctr_pts, _ptc_color, thickness=1, alpha=0.20)
        _draw_edge_polyline(out, pat_ctr_pts, _ptc_color, thickness=1, alpha=0.85, dot_radius=0)
    else:
        cv2.line(out, (hx, 0), (hx, h - 1), _ptc_color, 1)

    # Flecha central de offset (sheet-center → pattern-center) ELIMINADA a pedido:
    # molestaba para ver el patrón. La info de inclinación queda solo como número
    # arriba-izquierda (draw_tilt_indicator) y el offset en el texto de abajo.
    color = (0, 200, 0) if centering.within_tol else (0, 0, 255)

    # --- Text: margins, delta, offset, verticality ---
    lm = centering.left_margin_px
    rm = centering.right_margin_px
    delta = centering.margin_delta_px
    offset = centering.offset_px
    sign_d = "+" if delta >= 0 else ""
    sign_o = "+" if offset >= 0 else ""

    text_y_base = h - 10
    # Row 1 (bottom): margins left / right
    cv2.putText(out, f"Izq: {lm:.0f}px   Der: {rm:.0f}px",
                (10, text_y_base), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 180), 1, cv2.LINE_AA)
    # Row 2: delta + offset
    cv2.putText(out, f"Delta: {sign_d}{delta:.1f}px   Offset: {sign_o}{offset:.1f}px",
                (10, text_y_base - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    # Row 3: pattern edge verticality
    pl_slope = getattr(centering, "pattern_left_slope_deg", 0.0)
    pr_slope = getattr(centering, "pattern_right_slope_deg", 0.0)
    ps_delta = getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0)
    vert_color = (80, 200, 255)
    cv2.putText(out, f"Vert pat: Izq={pl_slope:+.1f}\xb0  Der={pr_slope:+.1f}\xb0  dCh={ps_delta:.1f}\xb0",
                (10, text_y_base - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.38, vert_color, 1, cv2.LINE_AA)

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
