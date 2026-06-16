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
    """Draw a semi-transparent NOK reasons panel anchored at y_start in the top-left corner."""
    if not reasons:
        return

    font      = cv2.FONT_HERSHEY_SIMPLEX
    hdr_scale = 0.62
    hdr_thick = 2
    row_scale = 0.50
    row_thick = 1
    pad       = 10
    row_gap   = 6

    header = "  NOK"
    (hw, hh), hbl = cv2.getTextSize(header, font, hdr_scale, hdr_thick)

    row_sizes = [cv2.getTextSize(f"  > {r}", font, row_scale, row_thick) for r in reasons]
    row_h_max = max((s[0][1] + s[1]) for s in row_sizes) if row_sizes else 0

    panel_w = max(hw, max(s[0][0] for s in row_sizes)) + pad * 2
    panel_h = (pad + hh + hbl + pad // 2
               + len(reasons) * (row_h_max + row_gap)
               + pad)

    y0, y1 = y_start, y_start + panel_h

    # Semi-transparent background
    layer = img.copy()
    cv2.rectangle(layer, (0, y0), (panel_w, y1), (20, 10, 80), -1)
    cv2.addWeighted(layer, 0.82, img, 0.18, 0, img)

    # Accent border
    cv2.line(img, (0, y0), (panel_w, y0), (60, 30, 220), 3)
    cv2.line(img, (0, y1), (panel_w, y1), (60, 30, 220), 2)
    cv2.line(img, (panel_w, y0), (panel_w, y1), (60, 30, 220), 2)

    # Header "NOK" — white text with red shadow
    y = y0 + pad + hh
    cv2.putText(img, header, (pad + 2, y + 2), font, hdr_scale, (0, 0, 120), hdr_thick + 2, cv2.LINE_AA)
    cv2.putText(img, header, (pad, y),          font, hdr_scale, (255, 255, 255), hdr_thick, cv2.LINE_AA)

    # Separator line
    cv2.line(img, (pad, y + hbl + 2), (panel_w - pad, y + hbl + 2), (80, 50, 200), 1)

    # Reason rows
    y += hbl + pad // 2
    for r in reasons:
        text = f"  > {r}"
        (_, rh), rbl = cv2.getTextSize(text, font, row_scale, row_thick)
        y += rh
        cv2.putText(img, text, (pad + 1, y + 1), font, row_scale, (0, 0, 80),   row_thick, cv2.LINE_AA)
        cv2.putText(img, text, (pad,     y),      font, row_scale, (80, 200, 255), row_thick, cv2.LINE_AA)
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
    """Draw inspection overlay."""
    out = img_bgr.copy()

    # Near-miss lines: expected → nearest detected (outside tolerance)
    for (ex, ey), (dx, dy) in near_miss_pairs:
        cv2.line(out, (int(ex), int(ey)), (int(dx), int(dy)),
                 (255, 220, 0), 1, cv2.LINE_AA)

    # Raw detected outside comparison window: dim cyan
    detected_keys = {
        (round(float(h.x), 2), round(float(h.y), 2), round(float(h.r), 2))
        for h in detected
    }
    for h in raw_detected:
        key = (round(float(h.x), 2), round(float(h.y), 2), round(float(h.r), 2))
        if key in detected_keys:
            continue
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (255, 255, 0), 1)

    # Detected holes used in comparison: bright green
    for h in detected:
        cv2.circle(out, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 2)

    # Missing expected holes: red ring + X marker + number
    for i, (x, y) in enumerate(missing_points):
        ix, iy = int(x), int(y)
        cv2.circle(out, (ix, iy), 10, (0, 0, 0),   2)
        cv2.circle(out, (ix, iy), 10, (0, 0, 255),  2)
        cv2.drawMarker(out, (ix, iy), (0, 0, 0),
                       markerType=cv2.MARKER_TILTED_CROSS, markerSize=16, thickness=3)
        cv2.drawMarker(out, (ix, iy), (0, 0, 255),
                       markerType=cv2.MARKER_TILTED_CROSS, markerSize=16, thickness=2)
        label = str(i + 1)
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
        cv2.putText(out, label, (ix - lw // 2, iy + lh // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0),     2, cv2.LINE_AA)
        cv2.putText(out, label, (ix - lw // 2, iy + lh // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

    # Extra detected (spurious): orange diamond
    for (x, y) in extra_points:
        cv2.drawMarker(out, (int(x), int(y)), (0, 165, 255),
                       markerType=cv2.MARKER_DIAMOND, markerSize=12, thickness=2)

    if draw_status:
        draw_status_indicator(out, status, nok_reasons, nok_panel_badge_count)

    return out


def draw_status_indicator(
    img: np.ndarray,
    status: str,
    nok_reasons: List[str] = (),
    badge_count: int = 0,
) -> np.ndarray:
    """Draw status indicator anchored to the left edge of the full frame."""
    if status == "NOK" and nok_reasons:
        y_start = max(0, int(badge_count) * _BADGE_H)
        _draw_nok_reasons_panel(img, list(nok_reasons), y_start=y_start)
    else:
        # Clean pill badge for OK
        font = cv2.FONT_HERSHEY_SIMPLEX
        label = "OK"
        scale, thick = 0.65, 2
        (tw, th), bl = cv2.getTextSize(label, font, scale, thick)
        pad = 8
        bx1, by1 = 8, 8
        bx2, by2 = bx1 + tw + pad * 2, by1 + th + bl + pad
        layer = img.copy()
        cv2.rectangle(layer, (bx1, by1), (bx2, by2), (0, 80, 0), -1)
        cv2.addWeighted(layer, 0.85, img, 0.15, 0, img)
        cv2.rectangle(img, (bx1, by1), (bx2, by2), (0, 220, 0), 2)
        cv2.putText(img, label, (bx1 + pad, by1 + th + 3),
                    font, scale, (0, 255, 0), thick, cv2.LINE_AA)
    return img


def draw_tilt_indicator(
    img: np.ndarray,
    tilt_deg: float,
    warn: bool = False,
    y: int = 62,
) -> np.ndarray:
    """Show tilt badge at bottom-left only when warn=True. Silent when OK."""
    import math
    if not warn:
        return img
    if tilt_deg is None or (isinstance(tilt_deg, float) and math.isnan(tilt_deg)):
        return img
    h_img = img.shape[0]
    label = f"! CHAPA INCLINADA  {tilt_deg:+.1f} deg"
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), bl = cv2.getTextSize(label, font, 0.52, 2)
    bx, by = 10, h_img - th - bl - 16
    cv2.rectangle(img, (bx, by), (bx + tw + 12, by + th + bl + 8), (0, 0, 130), -1)
    cv2.rectangle(img, (bx, by), (bx + tw + 12, by + th + bl + 8), (0, 0, 255), 2)
    cv2.putText(img, label, (bx + 6, by + th + 3),
                font, 0.52, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def draw_blur_indicator(
    img: np.ndarray,
    blur_score: float,
    blur_score_min: float,
    y: int = 82,
) -> np.ndarray:
    """Show blur badge at bottom-left only when frame is blurry. Silent when OK."""
    if blur_score_min <= 0:
        return img
    import math
    is_blurry = blur_score < blur_score_min
    if not is_blurry:
        return img
    h_img = img.shape[0]
    label = f"! IMAGEN BORROSA  ({blur_score:.0f})"
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), bl = cv2.getTextSize(label, font, 0.52, 2)
    # Place above tilt badge (reserve 40px gap)
    bx, by = 10, h_img - th - bl - 58
    cv2.rectangle(img, (bx, by), (bx + tw + 12, by + th + bl + 8), (0, 0, 130), -1)
    cv2.rectangle(img, (bx, by), (bx + tw + 12, by + th + bl + 8), (0, 0, 255), 2)
    cv2.putText(img, label, (bx + 6, by + th + 3),
                font, 0.52, (255, 200, 80), 2, cv2.LINE_AA)
    return img


def draw_roi_health_indicator(
    img: np.ndarray,
    roi_info,
    y: int = 102,
) -> np.ndarray:
    """Retained for API compatibility; no longer draws anything on the frame."""
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
    cv2.addWeighted(layer, 0.06, out, 0.94, 0, out)
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 200), 1)
    return out


def draw_machine_stop_badge(
    img: np.ndarray,
    reason: str = "",
    index: int = 0,
    title: str = "! DETENCION DE MAQUINA",
) -> np.ndarray:
    """Draw compact strip at the top of the image.

    index: stacking order — 0 = topmost strip, 1 = immediately below the first.
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

    # Border
    cv2.line(out, (0, banner_y + banner_h), (w, banner_y + banner_h), (0, 0, 255), 3)
    if banner_y > 0:
        cv2.line(out, (0, banner_y), (w, banner_y), (0, 0, 180), 2)

    # Main text — centered
    mx = w // 2 - mw // 2
    my = banner_y + pad_v + mh
    cv2.putText(out, main_label, (mx + 2, my + 2), font, main_scale,
                (0, 0, 60), main_thick + 3, cv2.LINE_AA)
    cv2.putText(out, main_label, (mx, my), font, main_scale,
                (255, 255, 255), main_thick, cv2.LINE_AA)

    # Reason text — amber, centered
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
    """Draw metal edge lines, pattern bound lines, and center lines.

    Removed: raw pixel text values (Izq/Der margins, Delta/Offset, Vert pat rows).
    Kept: visual polylines only. When pattern_warn=True, adds a compact metric chip.
    """
    out = img_bgr.copy()
    h, w = out.shape[:2]

    cx  = int(round(centering.sheet_center_x  + roi_x))
    hx  = int(round(centering.holes_center_x  + roi_x))

    lx  = int(round(centering.left_x          + roi_x))
    rx  = int(round(centering.right_x         + roi_x))
    plx = int(round(centering.pattern_left_x  + roi_x))
    prx = int(round(centering.pattern_right_x + roi_x))

    _off   = (roi_x, roi_y)
    _shift = lambda pts: tuple((x + _off[0], y + _off[1]) for x, y in pts)  # noqa: E731
    left_pts        = _shift(getattr(centering, "left_edge_points",     ()))
    right_pts       = _shift(getattr(centering, "right_edge_points",    ()))
    pat_l_pts       = _shift(getattr(centering, "pattern_left_points",  ()))
    pat_r_pts       = _shift(getattr(centering, "pattern_right_points", ()))
    sheet_ctr_pts   = _shift(getattr(centering, "sheet_center_points",  ()))
    pat_ctr_pts     = _shift(getattr(centering, "pattern_center_points", ()))

    _font_sm = cv2.FONT_HERSHEY_SIMPLEX

    # Metal edges (CHAPA)
    _edge_color = (180, 180, 180)
    lx_vis = max(3, min(lx, w - 55))
    rx_vis = max(3, min(rx, w - 55))
    if len(left_pts) >= 2:
        _draw_edge_polyline(out, left_pts, _edge_color, thickness=2, alpha=0.45)
    else:
        _draw_transparent_line(out, (lx, 0), (lx, h - 1), _edge_color, 2, 0.40)
    cv2.putText(out, "CHAPA", (lx_vis, 22), _font_sm, 0.38, _edge_color, 1, cv2.LINE_AA)

    if len(right_pts) >= 2:
        _draw_edge_polyline(out, right_pts, _edge_color, thickness=2, alpha=0.45)
    else:
        _draw_transparent_line(out, (rx, 0), (rx, h - 1), _edge_color, 2, 0.40)
    cv2.putText(out, "CHAPA", (rx_vis, 22), _font_sm, 0.38, _edge_color, 1, cv2.LINE_AA)

    # Pattern bounds (PATRON)
    _pat_color      = (50, 210, 220)   # normal: teal
    _pat_warn_color = (0, 110, 255)    # warning: vivid orange (BGR)
    pat_color  = _pat_warn_color if pattern_warn else _pat_color
    pat_thick  = 2 if pattern_warn else 1
    pat_alpha  = 0.90 if pattern_warn else 0.60
    pat_label  = "PATRON !!" if pattern_warn else "PATRON"
    pat_lw     = 2 if pattern_warn else 1

    for pts, fallback_x in ((pat_l_pts, plx), (pat_r_pts, prx)):
        x_vis = max(3, min(fallback_x, w - 70))
        if len(pts) >= 2:
            if pattern_warn:
                _draw_edge_polyline(out, pts, (0, 0, 70),
                                    thickness=pat_thick + 4, alpha=0.60, dot_radius=0)
            _draw_edge_polyline(out, pts, pat_color,
                                thickness=pat_thick, alpha=pat_alpha, dot_radius=3)
            if pattern_warn and len(pts) >= 3:
                xs = [p[0] for p in pts]
                mean_x = sum(xs) / len(xs)
                wi = max(range(len(pts)), key=lambda i: abs(pts[i][0] - mean_x))
                wp = (int(round(pts[wi][0])), int(round(pts[wi][1])))
                cv2.circle(out, wp, 18, (0, 0, 200), 3)
                cv2.circle(out, wp, 20, (255, 255, 255), 1)
                cv2.putText(out, "!", (wp[0] - 6, wp[1] + 8),
                            _font_sm, 0.8, (0, 0, 255), 3, cv2.LINE_AA)
        cv2.putText(out, pat_label, (x_vis, 36), _font_sm, 0.38, pat_color, pat_lw, cv2.LINE_AA)

    # Sheet center line (orange)
    _shc_color = (0, 150, 255)
    if len(sheet_ctr_pts) >= 2:
        _draw_full_height_fit_line(out, sheet_ctr_pts, _shc_color, thickness=1, alpha=0.25)
        _draw_edge_polyline(out, sheet_ctr_pts, _shc_color, thickness=2, alpha=0.70, dot_radius=0)
    else:
        for yy in range(0, h, 20):
            cv2.line(out, (cx, yy), (cx, min(yy + 10, h - 1)), _shc_color, 1)

    # Pattern center line (white, subtle)
    _ptc_color = (220, 220, 220)
    if len(pat_ctr_pts) >= 2:
        _draw_full_height_fit_line(out, pat_ctr_pts, _ptc_color, thickness=1, alpha=0.18)
        _draw_edge_polyline(out, pat_ctr_pts, _ptc_color, thickness=1, alpha=0.75, dot_radius=0)
    else:
        _draw_transparent_line(out, (hx, 0), (hx, h - 1), _ptc_color, 1, 0.18)

    # Compact metric chip (bottom-right) only when pattern_warn=True
    if pattern_warn:
        ps_delta = getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0)
        zigzag_max = getattr(centering, "pattern_zigzag_max_px", 0.0)
        chip_text = f"zigzag {zigzag_max:.1f}px  slope {ps_delta:.1f}deg"
        font = cv2.FONT_HERSHEY_SIMPLEX
        cscale, cthick = 0.40, 1
        (cw, ch_), cbl = cv2.getTextSize(chip_text, font, cscale, cthick)
        pad = 5
        cx1 = w - cw - pad * 2 - 4
        cy1 = h - ch_ - cbl - pad * 2 - 4
        cx2, cy2 = w - 4, h - 4
        layer = out.copy()
        cv2.rectangle(layer, (cx1, cy1), (cx2, cy2), (0, 40, 100), -1)
        cv2.addWeighted(layer, 0.75, out, 0.25, 0, out)
        cv2.rectangle(out, (cx1, cy1), (cx2, cy2), (0, 110, 255), 1)
        cv2.putText(out, chip_text, (cx1 + pad, cy2 - cbl - 3),
                    font, cscale, (80, 200, 255), cthick, cv2.LINE_AA)

    # "BORDES NO CONFIABLES" badge
    centering_reliable = getattr(centering, "centering_reliable", True)
    if not centering_reliable:
        label_nc = "BORDES NO CONFIABLES"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale_nc, thick_nc = 0.65, 2
        (tw, th), bl = cv2.getTextSize(label_nc, font, scale_nc, thick_nc)
        bx1, by1 = w // 2 - tw // 2 - 8, 140
        bx2, by2 = w // 2 + tw // 2 + 8, 140 + th + bl + 8
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 80, 160), -1)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 165, 255), 2)
        cv2.putText(out, label_nc, (bx1 + 8, by2 - bl - 4),
                    font, scale_nc, (255, 255, 255), thick_nc, cv2.LINE_AA)

    # "NOK CENTRADO" badge
    if tag_nok:
        label = "NOK CENTRADO"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thick = 1.0, 3
        (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
        bx1, by1 = w // 2 - tw // 2 - 8, 105
        bx2, by2 = w // 2 + tw // 2 + 8, 105 + th + baseline + 10
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 160), -1)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 255), 2)
        cv2.putText(out, label, (bx1 + 8, by2 - baseline - 4),
                    font, scale, (255, 255, 255), thick, cv2.LINE_AA)

    return out


def _draw_edge_polyline(
    img: np.ndarray,
    points: Sequence[Tuple[float, float]],
    color: tuple,
    thickness: int = 2,
    alpha: float = 0.55,
    dot_radius: int = 3,
) -> None:
    if len(points) < 1:
        return
    sorted_pts = sorted(points, key=lambda p: p[1])

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
