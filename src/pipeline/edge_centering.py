"""
Detects the left and right edges of the metal sheet and measures whether
the hole pattern is centered between them.

When a ROI is provided (the common case), edges are detected using narrow
search windows around the ROI boundaries in the FULL-FRAME image, where the
backlights are visible. A gradient-based approach (bright->dark for the left
edge, dark->bright for the right) locates the actual metal-to-backlight
transition per horizontal band.

When no ROI is given, falls back to column-profile threshold detection on
whatever image is passed (legacy path, works when backlight is in frame).

All coordinates returned in CenteringResult are in ROI space (same space as
hole coordinates), so the drawing overlay works without extra transforms.

Edge detection strategy (with ROI):
  - Full frame divided into N_BANDS horizontal bands (only within ROI y-extent).
  - Per band, a narrow search window is taken from the full-frame R channel.
  - Rows downsampled DS for speed; column mean profile -> smoothed -> gradient.
  - Left: sharpest bright->dark drop; Right: sharpest dark->bright rise.
  - Per-band edge X is converted to ROI-relative before storing.
  - A robust line (sigma-clip polyfit) is fitted to per-band edge points.
  - The scalar left_x / right_x are evaluated at mid-height from that line.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_N_BANDS = 16
_MIN_RELIABLE_BANDS = 6

# Search window half-widths (px, full-frame coords) around ROI edges.
_LEFT_INNER  =  80   # how far inside  the ROI left  edge to search
_LEFT_OUTER  = 350   # how far outside the ROI left  edge to search
_RIGHT_INNER =  80   # how far inside  the ROI right edge to search
_RIGHT_OUTER = 350   # how far outside the ROI right edge to search

# Row downsampling factor for speed (4 = 4x fewer rows to average per band).
_DS = 4

# Smoothing kernel for column profile (small to preserve gradient magnitude).
_SMOOTH_K = 7

# Validación de candidato a borde por lados sostenidos (px de la ventana).
# El lado metal debe ser oscuro sostenido y el lado backlight brillante
# sostenido; agujeros del patrón y lavados de luz sobre el metal fallan
# ambas condiciones, el borde real las cumple siempre.
_RUN_PX  = 20   # largo de la corrida a promediar a cada lado
_RUN_GAP = 4    # separación entre el candidato y el inicio de la corrida
_RUN_MIN = 8    # mínimo de px disponibles para validar una corrida
_DARK_FRAC   = 0.25  # umbral lado oscuro:   dark + 0.25*rng
_BRIGHT_FRAC = 0.35  # umbral lado brillante: bright - 0.35*rng


@dataclass(frozen=True)
class CenteringResult:
    left_x: float           # left metal edge X  (px, ROI coords) -- fitted line at mid-height
    right_x: float          # right metal edge X (px, ROI coords)
    sheet_center_x: float   # midpoint between sheet edges
    holes_center_x: float   # mean X of detected holes
    offset_px: float        # margin_delta_px / 2  (+ = pattern shifted right)
    sheet_width_px: float   # right_x - left_x
    within_tol: bool        # |offset_px| <= tol_px  (always True when tol_px == 0)
    pattern_left_x: float   # min(hole.x - hole.r) -- left physical bound of detected pattern
    pattern_right_x: float  # max(hole.x + hole.r) -- right physical bound of detected pattern
    left_margin_px: float   # pattern_left_x - sheet_left_x
    right_margin_px: float  # sheet_right_x - pattern_right_x
    margin_delta_px: float  # left_margin_px - right_margin_px (>0 = pattern shifted right)

    # Per-band real measurement points in ROI coords (tuple of (x, y) sorted by y)
    left_edge_points: tuple = field(default_factory=tuple)
    right_edge_points: tuple = field(default_factory=tuple)
    pattern_left_points: tuple = field(default_factory=tuple)
    pattern_right_points: tuple = field(default_factory=tuple)

    # Per-band margin variation (std dev across bands, 0 if < 2 matched bands)
    left_margin_std: float = 0.0
    right_margin_std: float = 0.0

    # False if too few bands were detected to trust the measurement
    centering_reliable: bool = True

    # Slope of each edge line from vertical (degrees). 0° = perfectly vertical.
    # Positive = edge leans right as Y increases. Measured from per-band points.
    left_edge_slope_deg: float = 0.0
    right_edge_slope_deg: float = 0.0
    pattern_left_slope_deg: float = 0.0
    pattern_right_slope_deg: float = 0.0

    # Horizontal residuals of per-band SHEET EDGE points from a robust fitted line.
    # High values indicate camera/sheet vibration (frame image quality issue).
    # 0.0 when insufficient points (<3) for a line fit.
    chapa_zigzag_std_px: float = 0.0
    chapa_zigzag_max_px: float = 0.0

    # Horizontal residuals of per-band PATTERN (hole) edge points from a robust fitted line.
    # High values indicate pattern misalignment within the sheet (mechanical issue).
    # 0.0 when insufficient points (<3) for a line fit.
    pattern_zigzag_std_px: float = 0.0
    pattern_zigzag_max_px: float = 0.0

    # Horizontal residuals of per-band PATTERN CENTER (median X of all holes per band).
    # Catches internal zigzag that the outer-border metric misses.
    # 0.0 when insufficient points (<3) for a line fit.
    pattern_center_zigzag_std_px: float = 0.0
    pattern_center_zigzag_max_px: float = 0.0

    # Global pattern-vs-sheet alignment metrics. These catch abrupt large
    # shifts or tilts even when the pattern itself remains straight.
    pattern_sheet_slope_delta_left_deg: float = 0.0
    pattern_sheet_slope_delta_right_deg: float = 0.0
    pattern_sheet_slope_delta_max_deg: float = 0.0

    # Per-band real center lines in ROI coords (tuple of (x, y) sorted by y).
    # sheet_center_points: midpoint between left and right CHAPA edges per band.
    # pattern_center_points: midpoint between left and right PATRON bounds per band.
    # Both are polylines — if the sheet/pattern is tilted they will slant visibly.
    sheet_center_points: tuple = field(default_factory=tuple)
    pattern_center_points: tuple = field(default_factory=tuple)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _sided_run_means(col_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Media sostenida a izquierda y derecha de cada posición del perfil.

    Devuelve (left_mean, right_mean, valid) donde left_mean[i] es la media de
    col_s[i-_RUN_GAP-_RUN_PX : i-_RUN_GAP] (recortada al borde) y right_mean[i]
    la simétrica. valid[i] es False cuando algún lado tiene menos de _RUN_MIN px.
    """
    n = len(col_s)
    cs = np.concatenate(([0.0], np.cumsum(col_s, dtype=np.float64)))
    idx = np.arange(n)

    l0 = np.clip(idx - _RUN_GAP - _RUN_PX, 0, n)
    l1 = np.clip(idx - _RUN_GAP, 0, n)
    r0 = np.clip(idx + 1 + _RUN_GAP, 0, n)
    r1 = np.clip(idx + 1 + _RUN_GAP + _RUN_PX, 0, n)

    l_len = (l1 - l0).astype(np.float64)
    r_len = (r1 - r0).astype(np.float64)
    valid = (l_len >= _RUN_MIN) & (r_len >= _RUN_MIN)

    left_mean = np.zeros(n, dtype=np.float64)
    right_mean = np.zeros(n, dtype=np.float64)
    nz_l = l_len > 0
    nz_r = r_len > 0
    left_mean[nz_l] = (cs[l1[nz_l]] - cs[l0[nz_l]]) / l_len[nz_l]
    right_mean[nz_r] = (cs[r1[nz_r]] - cs[r0[nz_r]]) / r_len[nz_r]
    return left_mean, right_mean, valid


def _find_edge_in_profile(col_s: np.ndarray, side: str) -> Optional[tuple[float, float]]:
    """Posición del borde metal/backlight en un perfil de columnas suavizado.

    side="left":  borde izquierdo de la chapa — brillante(afuera) -> oscuro(metal).
    side="right": borde derecho de la chapa — oscuro(metal) -> brillante(afuera).

    En vez del gradiente más fuerte de toda la ventana (que con blur o luz
    derramada sobre el borde cae en agujeros del patrón o en el lavado
    interior), se exige que el candidato tenga lado metal oscuro sostenido y
    lado backlight brillante sostenido. Entre los válidos se toma el MÁS
    INTERNO (el más cercano al metal): esa es la primera transición física
    real; los candidatos más externos son estructuras del resplandor o
    suciedad sobre el backlight. Los falsos internos (agujeros del patrón,
    lavado sobre el metal) ya fallan las compuertas de lados sostenidos.

    Devuelve (x, ancho_de_rampa) o None si ninguna posición califica (banda
    omitida). El ancho de rampa es el largo en px de la transición: las bandas
    con borde lavado por la luz tienen rampas mucho más anchas que las nítidas
    y el llamador puede descartarlas para las métricas sensibles (pendiente).
    """
    rng = float(col_s.max() - col_s.min())
    if rng < 20.0:
        return None
    grad = np.diff(col_s)
    if grad.size == 0:
        return None

    dark = float(np.percentile(col_s, 10))
    bright = float(np.percentile(col_s, 90))
    dark_thr = dark + _DARK_FRAC * rng
    bright_thr = bright - _BRIGHT_FRAC * rng

    left_mean, right_mean, valid = _sided_run_means(col_s)
    left_mean, right_mean, valid = left_mean[:-1], right_mean[:-1], valid[:-1]

    if side == "left":
        mask = (grad < -rng * 0.03) & valid \
            & (left_mean >= bright_thr) & (right_mean <= dark_thr)
    else:
        mask = (grad > rng * 0.03) & valid \
            & (left_mean <= dark_thr) & (right_mean >= bright_thr)

    idxs = np.where(mask)[0]
    if idxs.size == 0:
        return None

    # Agrupar índices consecutivos y quedarse con el grupo más INTERNO
    # (pegado al metal). En la ventana izquierda el metal queda a la derecha
    # (último grupo); en la ventana derecha, a la izquierda (primer grupo).
    splits = np.where(np.diff(idxs) > 3)[0] + 1
    groups = np.split(idxs, splits)
    group = groups[-1] if side == "left" else groups[0]

    # Expandir el grupo a la rampa completa del borde: con blur la validación
    # recién pasa al final de la rampa y usar ese índice sesga el punto hacia
    # afuera; la rampa entera es donde el gradiente sigue siendo significativo.
    sign_grad = -grad if side == "left" else grad
    seed = int(group[np.argmax(sign_grad[group])])
    ramp_thr = rng * 0.015
    lo = seed
    gap = 0
    while lo - 1 >= 0 and gap <= 3:
        lo -= 1
        gap = gap + 1 if sign_grad[lo] <= ramp_thr else 0
    hi = seed
    gap = 0
    while hi + 1 < len(sign_grad) and gap <= 3:
        hi += 1
        gap = gap + 1 if sign_grad[hi] <= ramp_thr else 0

    # Posición final: cruce del nivel medio dentro de la rampa, interpolado.
    # Es invariante al blur (el 50% de una transición simétrica cae en el
    # borde físico esté nítida o lavada), así bandas nítidas y borrosas del
    # mismo frame miden el mismo borde.
    mid = dark + 0.5 * (bright - dark)
    ramp_w = float(hi - lo + 1)
    seg = col_s[lo:hi + 2]
    if side == "right":
        above = np.where(seg >= mid)[0]
        if above.size and above[0] > 0:
            j = int(above[0])
            step = max(float(seg[j] - seg[j - 1]), 1e-6)
            return float(lo + j - 1) + float(mid - seg[j - 1]) / step, ramp_w
    else:
        below = np.where(seg < mid)[0]
        if below.size and below[0] > 0:
            j = int(below[0])
            step = max(float(seg[j - 1] - seg[j]), 1e-6)
            return float(lo + j - 1) + float(seg[j - 1] - mid) / step, ramp_w
    return float(seed), ramp_w


def _detect_sheet_edges_in_windows(
    img_full: np.ndarray,
    roi_x: int,
    roi_w: int,
    roi_y: int,
    roi_h: int,
    n_bands: int = _N_BANDS,
    ds: int = _DS,
    inner_px: int = _LEFT_INNER,
) -> tuple[
    dict[int, tuple[float, float]],
    dict[int, tuple[float, float]],
    dict[int, float],
    dict[int, float],
]:
    """Per-band left/right metal edge detection in search windows.

    Works on the full-frame R channel (backlight is red/bright).
    Returns (left_dict, right_dict, left_ramp, right_ramp): the first two keyed
    by band index with values (x_roi_relative, y_roi_relative); the ramp dicts
    hold the edge transition width in px per band (wide = washed-out edge).
    Bands where no clear bright/dark transition is found are omitted.

    Left window covers [roi_x - _LEFT_OUTER, roi_x + inner_px] in full-frame x.
    Right window covers [roi_x + roi_w - inner_px, roi_x + roi_w + _RIGHT_OUTER].
    The gradient peak (bright->dark for left, dark->bright for right) locates the edge.
    Reduce inner_px to avoid capturing hole-related gradients inside the ROI.
    """
    H, W = img_full.shape[:2]
    r_ch = img_full[:, :, 2].astype(np.float32)   # R channel

    lw_x0 = max(0, roi_x - _LEFT_OUTER)
    lw_x1 = min(W - 1, roi_x + inner_px)
    rw_x0 = max(0, roi_x + roi_w - inner_px)
    rw_x1 = min(W - 1, roi_x + roi_w + _RIGHT_OUTER)

    band_h = roi_h / n_bands
    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}
    left_ramp:  dict[int, float] = {}
    right_ramp: dict[int, float] = {}

    for i in range(n_bands):
        y0_full = roi_y + int(i * band_h)
        y1_full = roi_y + min(int((i + 1) * band_h), roi_h)
        if y1_full - y0_full < 4:
            continue

        cy_roi = float((y0_full - roi_y) + (y1_full - roi_y)) / 2.0

        # Left edge: outermost validated bright->dark transition
        if lw_x1 > lw_x0 + 4:
            strip = r_ch[y0_full:y1_full:ds, lw_x0:lw_x1]
            if strip.size == 0:
                continue
            col_prof = strip.mean(axis=0)
            # Small kernel: rows are already averaged so noise is low.
            # A large kernel would spread the edge and kill the gradient.
            if len(col_prof) > _SMOOTH_K:
                col_s = cv2.GaussianBlur(
                    col_prof.reshape(1, -1), (_SMOOTH_K, 1), 0
                ).ravel()
            else:
                col_s = col_prof
            found = _find_edge_in_profile(col_s, side="left")
            if found is not None:
                left_dict[i] = (float(lw_x0) + found[0] - roi_x, cy_roi)
                left_ramp[i] = found[1]

        # Right edge: outermost validated dark->bright transition
        if rw_x1 > rw_x0 + 4:
            strip = r_ch[y0_full:y1_full:ds, rw_x0:rw_x1]
            if strip.size == 0:
                continue
            col_prof = strip.mean(axis=0)
            if len(col_prof) > _SMOOTH_K:
                col_s = cv2.GaussianBlur(
                    col_prof.reshape(1, -1), (_SMOOTH_K, 1), 0
                ).ravel()
            else:
                col_s = col_prof
            found = _find_edge_in_profile(col_s, side="right")
            if found is not None:
                right_dict[i] = (float(rw_x0) + found[0] - roi_x, cy_roi)
                right_ramp[i] = found[1]

    return left_dict, right_dict, left_ramp, right_ramp


def _background_threshold(gray: np.ndarray, w: int) -> float:
    """Estimate metal-vs-background threshold from full-image column profile."""
    col_full = np.percentile(gray, 20, axis=0).astype(np.float32)
    k = max(5, w // 30)
    k = k + 1 if k % 2 == 0 else k
    col_smooth = cv2.GaussianBlur(col_full.reshape(1, -1), (k, 1), 0).ravel()
    bg_level = float(np.percentile(col_smooth, 95))
    return bg_level * 0.70


def _detect_edges_by_band(
    img_bgr: np.ndarray,
    n_bands: int = _N_BANDS,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Legacy band-based edge detection (threshold on column profile).

    Used only when no ROI is provided (roi=None path).
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    metal_thresh = _background_threshold(gray, w)
    band_h = h / n_bands
    min_metal_cols = max(5, int(w * 0.05))

    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0 = int(i * band_h)
        y1 = min(int((i + 1) * band_h), h)
        if y1 - y0 < 8:
            continue

        band = gray[y0:y1, :]
        col_pct = np.percentile(band, 20, axis=0).astype(np.float32)

        k_b = max(3, w // 60)
        k_b = k_b + 1 if k_b % 2 == 0 else k_b
        col_b = cv2.GaussianBlur(col_pct.reshape(1, -1), (k_b, 1), 0).ravel()

        metal_cols = np.where(col_b < metal_thresh)[0]
        if len(metal_cols) < min_metal_cols:
            continue

        cy = (y0 + y1) / 2.0
        left_dict[i]  = (float(metal_cols[0]),  cy)
        right_dict[i] = (float(metal_cols[-1]), cy)

    return left_dict, right_dict


def _iqr_filter_band_dict(
    d: dict[int, tuple[float, float]],
    iqr_factor: float = 2.5,
) -> dict[int, tuple[float, float]]:
    """Remove bands whose measured edge x is an IQR outlier.

    Bands where no outermost-column hole fell (e.g. due to staggered grid
    row gaps) occasionally pick an interior hole as the edge; those produce
    x positions far from the median and are safely rejected here.
    Returns the original dict unchanged when fewer than 4 bands exist (not
    enough data for a meaningful IQR estimate).
    """
    if len(d) < 4:
        return d
    xs = np.array([v[0] for v in d.values()], dtype=np.float64)
    q1 = float(np.percentile(xs, 25))
    q3 = float(np.percentile(xs, 75))
    iqr = q3 - q1
    if iqr < 1.0:
        return d  # all very similar — nothing to filter
    lo = q1 - iqr_factor * iqr
    hi = q3 + iqr_factor * iqr
    return {k: v for k, v in d.items() if lo <= v[0] <= hi}


def _trim_y_extremes(
    d: dict[int, tuple[float, float]],
    trim_frac: float = 0.10,
) -> dict[int, tuple[float, float]]:
    """Drop bands in the outer trim_frac of the measured y range.

    The top and bottom of the pattern have fewer hole rows per band and are
    more sensitive to the staggered-column alternation, producing noisy edge
    estimates that inflate the zigzag metric even on a good frame.  Trimming
    the outer 10 % of y extent removes these without affecting the main body
    of the pattern.  Does nothing when fewer than 4 bands exist.
    """
    if len(d) < 4:
        return d
    ys = [v[1] for v in d.values()]
    y_min, y_max = min(ys), max(ys)
    y_range = y_max - y_min
    if y_range < 1.0:
        return d
    lo = y_min + trim_frac * y_range
    hi = y_max - trim_frac * y_range
    return {k: v for k, v in d.items() if lo <= v[1] <= hi}


def _split_holes_into_y_rows(
    holes: Sequence,
    row_tol_px: float,
) -> list[list]:
    """Cluster holes into actual Y rows using a small vertical tolerance."""
    if not holes:
        return []
    sorted_holes = sorted(holes, key=lambda hh: float(hh.y))
    rows: list[list] = [[sorted_holes[0]]]
    for hh in sorted_holes[1:]:
        cur_row = rows[-1]
        cur_y = float(np.median([pt.y for pt in cur_row]))
        if abs(float(hh.y) - cur_y) <= row_tol_px:
            cur_row.append(hh)
        else:
            rows.append([hh])
    return rows


def _robust_outer_column_centers(holes: Sequence) -> tuple[float, float]:
    """Estimate the true outermost pattern columns from X-center clusters.

    Percentile-based references work when sparse outlier columns exist, but they fail
    when the outermost real column is dense: q10/q90 can land on the 2nd column and the
    boundary gate then mixes two adjacent columns, creating a visible zigzag.

    We cluster hole centers by X and choose the outermost *dominant* cluster. If the
    extreme cluster is much sparser than its neighbour, treat it as an outlier and use
    the next cluster inward instead. This keeps microperforado on the real border column
    while still ignoring tiny sparse outlier columns.
    """
    all_holes = list(holes)
    if not all_holes:
        return 0.0, 0.0

    xs = sorted(float(hh.x) for hh in all_holes)
    if len(xs) == 1:
        return xs[0], xs[0]

    median_r = float(np.median([max(float(getattr(hh, "r", 0.0)), 1.0) for hh in all_holes]))
    cluster_gap = max(6.0, median_r * 1.25)

    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if abs(x - clusters[-1][-1]) <= cluster_gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    cluster_stats = [
        {"mean": float(np.mean(c)), "count": len(c)}
        for c in clusters
    ]

    def _pick(stats: list[dict], reverse: bool = False) -> float:
        ordered = list(reversed(stats)) if reverse else list(stats)
        if len(ordered) == 1:
            return float(ordered[0]["mean"])
        outer = ordered[0]
        inner = ordered[1]
        if outer["count"] < inner["count"] * 0.6:
            return float(inner["mean"])
        return float(outer["mean"])

    return _pick(cluster_stats, reverse=False), _pick(cluster_stats, reverse=True)


def _pattern_bounds_by_band(
    holes: Sequence,
    img_h: int,
    n_bands: int = _N_BANDS,
    min_holes: int = 1,
    boundary_tol_px: float = 0.0,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Per-band left/right physical bounds using detected hole positions.

    Y coordinate of each band point is the mean Y of actual holes in that
    band (not the band centre), so measurements always land on real hole rows
    and never between two rows.

    After collection two filters run in sequence:
    1. IQR filter: removes bands where an interior hole was picked as edge
       (x-position outlier) — common when no outermost-column hole falls in
       a band for staggered grids.
    2. Y-extremes trim: drops the outer 10 % of the y range (top and bottom)
       where sparse row coverage produces noisy single-point estimates that
       inflate the zigzag metric even on good frames.
    """
    if not holes:
        return {}, {}

    all_holes = list(holes)
    global_left, global_right = _robust_outer_column_centers(all_holes)
    band_h = img_h / n_bands
    row_tol_px = max(
        3.0,
        min(
            band_h * 0.45,
            float(np.median([hh.r for hh in all_holes])) * 1.6,
        ),
    )
    left_dict:  dict[int, tuple[float, float]] = {}
    right_dict: dict[int, tuple[float, float]] = {}

    for i in range(n_bands):
        y0 = i * band_h
        y1 = (i + 1) * band_h
        band_mid = (y0 + y1) / 2.0

        band_holes = [hh for hh in all_holes if y0 <= hh.y < y1]
        if len(band_holes) >= min_holes:
            row_groups = _split_holes_into_y_rows(band_holes, row_tol_px=row_tol_px)

            left_candidates: list[tuple[float, float, float]] = []
            right_candidates: list[tuple[float, float, float]] = []
            for row_holes in row_groups:
                # Center-based boundary gate: only keep holes whose X centre is
                # within boundary_tol_px of the globally outermost centre.  This
                # selects exactly the outermost column (for tight tol values well
                # below the stagger offset) so the resulting line always passes
                # through hole centres and never falls between two columns.
                if boundary_tol_px > 0.0:
                    left_holes = [
                        hh for hh in row_holes
                        if hh.x <= global_left + boundary_tol_px
                    ]
                    right_holes = [
                        hh for hh in row_holes
                        if hh.x >= global_right - boundary_tol_px
                    ]
                else:
                    left_holes = row_holes
                    right_holes = row_holes

                if len(left_holes) >= min_holes:
                    cy_left = float(np.median([hh.y for hh in left_holes]))
                    left_x = float(min(hh.x for hh in left_holes))
                    left_candidates.append((abs(cy_left - band_mid), left_x, cy_left))
                if len(right_holes) >= min_holes:
                    cy_right = float(np.median([hh.y for hh in right_holes]))
                    right_x = float(max(hh.x for hh in right_holes))
                    right_candidates.append((abs(cy_right - band_mid), right_x, cy_right))

            # Choose the real hole row closest to the band center, so Y always lands
            # on an actual row instead of the empty gap between two rows.
            if left_candidates:
                _, left_x, cy_left = min(left_candidates, key=lambda item: item[0])
                left_dict[i] = (left_x, cy_left)
            if right_candidates:
                _, right_x, cy_right = min(right_candidates, key=lambda item: item[0])
                right_dict[i] = (right_x, cy_right)

    left_dict  = _iqr_filter_band_dict(left_dict)
    right_dict = _iqr_filter_band_dict(right_dict)
    return left_dict, right_dict


def _pattern_center_by_band(
    left_by_band: dict[int, tuple[float, float]],
    right_by_band: dict[int, tuple[float, float]],
) -> list[tuple[float, float]]:
    """Per-band center between physical pattern bounds.

    Median X of all holes is too sensitive for staggered microperforated grids:
    alternating rows can move the median even when the physical pattern is fine.
    The center between left/right pattern bounds is steadier and still catches
    real lateral waviness.
    """
    points: list[tuple[float, float]] = []
    for i in sorted(set(left_by_band) & set(right_by_band)):
        lx, ly = left_by_band[i]
        rx, ry = right_by_band[i]
        points.append(((lx + rx) / 2.0, (ly + ry) / 2.0))
    return points


def _smooth_points_x(
    pts: list[tuple[float, float]],
    window: int,
) -> list[tuple[float, float]]:
    """Sliding median of `window` over the x-values of (x, y) points sorted by y.

    Applied to pattern edge/center series before computing zigzag residuals so
    that isolated outlier bands (e.g. a band with very few holes near the top
    or bottom of the ROI) don't inflate the metric.  The y positions are kept
    unchanged so the line-fit geometry is not disturbed.

    Returns the original list unchanged when window <= 1 or len(pts) < window.
    """
    if window <= 1 or len(pts) < window:
        return pts
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = [p[1] for p in pts]
    half = window // 2
    xs_out = np.empty_like(xs)
    for k in range(len(xs)):
        lo = max(0, k - half)
        hi = min(len(xs), k + half + 1)
        xs_out[k] = float(np.median(xs[lo:hi]))
    return [(float(x), float(y)) for x, y in zip(xs_out, ys)]


def _fit_line_robust(
    points: list[tuple[float, float]],
    sigma: float = 2.0,
) -> Optional[tuple[float, float]]:
    """Fit x = a*y + b with sigma-clip outlier rejection. Returns (a, b) or None."""
    if len(points) < 4:
        return None
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    try:
        coeffs = np.polyfit(ys, xs, 1)
    except Exception:
        return None

    residuals = np.abs(xs - np.polyval(coeffs, ys))
    std = float(np.std(residuals))
    if std > 0:
        inliers = residuals < sigma * std
        if inliers.sum() >= 4:
            try:
                coeffs = np.polyfit(ys[inliers], xs[inliers], 1)
            except Exception:
                pass

    return float(coeffs[0]), float(coeffs[1])


def _line_x_at_y(coeffs: tuple[float, float], y: float) -> float:
    return coeffs[0] * y + coeffs[1]


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def compute_centering(
    img_bgr: np.ndarray,
    holes: Sequence,
    roi=None,
    tol_px: float = 0.0,
    n_bands: int = _N_BANDS,
    min_holes_per_band: int = 1,
    smooth_window: int = 1,
    boundary_tol_px: float = 0.0,
    chapa_inner_px: int = _LEFT_INNER,
) -> Optional[CenteringResult]:
    """Measure how centered the hole pattern is between the metal sheet edges.

    Parameters
    ----------
    img_bgr:
        When roi is provided: the FULL-FRAME aligned image (both backlights
        must be visible in the frame even though they are outside the ROI).
        When roi is None: the image that contains visible sheet edges
        (legacy path, works when backlight is inside the frame).
    holes:
        Sequence of Hole objects with .x, .y, .r in ROI-relative coordinates.
    roi:
        Optional ROI object with .x, .y, .w, .h (full-frame coords).
        When provided, sheet edges are detected in search windows around the
        ROI boundaries so that the backlights (outside the ROI) are visible.
    tol_px:
        Tolerance in pixels. 0 = measure only, never triggers NOK.
    n_bands:
        Number of horizontal bands for edge/pattern sampling. More bands give
        finer spatial resolution but each band covers fewer hole rows. If a
        band ends up below min_holes_per_band it is silently excluded.
        Default 16 (backward-compatible). Configurable via edge_centering_bands.
    min_holes_per_band:
        Minimum detected holes required in a band to include it in the pattern
        edge/center series. Bands with fewer holes produce noisy single-point
        estimates; excluding them reduces zigzag false positives.
        Configurable via pattern_edge_min_holes_per_band.
    smooth_window:
        Sliding-median window size (in bands) applied to pattern edge and center
        point series BEFORE computing zigzag residuals. Suppresses isolated
        outlier bands without affecting the fitted-line geometry used for
        centering. Window=1 disables smoothing (backward-compatible).
        Configurable via pattern_edge_smooth_window.
    boundary_tol_px:
        When > 0, per-band left edge is computed only from holes whose physical
        left bound (x - r) is within this distance of the global leftmost bound,
        and symmetrically for the right edge. Prevents interior holes from being
        picked as the band edge when the outermost-column hole is absent from a
        band. Typical value: 1.5 × hole_radius, or ~half the inter-column dx.
        0 = use all holes (backward-compatible).
        Configurable via pattern_edge_boundary_tol_px.

    Returns None if no holes are provided or no edges can be detected at all.

    All coordinates in CenteringResult are in ROI space (same frame as holes).

    offset_px = margin_delta_px / 2, where:
      left_margin_px  = pattern_left_x  - left_x     (both ROI-relative)
      right_margin_px = right_x - pattern_right_x    (both ROI-relative)
      margin_delta_px = left_margin_px  - right_margin_px
    Positive offset_px means the pattern is shifted toward the right edge.
    """
    if not holes:
        return None

    h, w = img_bgr.shape[:2]

    if roi is not None:
        roi_x, roi_y = int(roi.x), int(roi.y)
        roi_w, roi_h = int(roi.w), int(roi.h)
        mid_y = roi_h / 2.0

        edge_left, edge_right, left_ramp, right_ramp = _detect_sheet_edges_in_windows(
            img_bgr, roi_x, roi_w, roi_y, roi_h, n_bands=n_bands,
            inner_px=chapa_inner_px,
        )
        # Values are already in ROI-relative coords
        img_h_for_bands = roi_h

    else:
        mid_y = h / 2.0
        edge_left, edge_right = _detect_edges_by_band(img_bgr, n_bands=n_bands)
        left_ramp, right_ramp = {}, {}
        img_h_for_bands = h

    # Red extra contra bandas donde el borde saltó hacia adentro (luz derramada
    # sobre el borde, reflejos): descarta outliers aislados en X. Factor 3.0
    # generoso para no enmascarar vibración real repartida en muchas bandas.
    edge_left  = _iqr_filter_band_dict(edge_left,  iqr_factor=3.0)
    edge_right = _iqr_filter_band_dict(edge_right, iqr_factor=3.0)
    left_pts_list  = list(edge_left.values())
    right_pts_list = list(edge_right.values())

    n_left  = len(left_pts_list)
    n_right = len(right_pts_list)
    centering_reliable = (n_left >= _MIN_RELIABLE_BANDS and n_right >= _MIN_RELIABLE_BANDS)

    logger.debug(
        "compute_centering: %d left-band pts, %d right-band pts, reliable=%s",
        n_left, n_right, centering_reliable,
    )

    # Scalar edge positions: robust fitted line > median > give up
    left_coeffs  = _fit_line_robust(left_pts_list)
    right_coeffs = _fit_line_robust(right_pts_list)

    if left_coeffs is not None and right_coeffs is not None:
        left_x  = _line_x_at_y(left_coeffs,  mid_y)
        right_x = _line_x_at_y(right_coeffs, mid_y)
    elif left_pts_list and right_pts_list:
        left_x  = float(np.median([p[0] for p in left_pts_list]))
        right_x = float(np.median([p[0] for p in right_pts_list]))
    else:
        logger.debug("compute_centering: no edges detected, returning None")
        return None

    sheet_center_x = (left_x + right_x) / 2.0
    holes_center_x = float(np.mean([hh.x for hh in holes]))

    # Pattern bounds from actual detected holes (ROI-relative coords)
    pat_left, pat_right = _pattern_bounds_by_band(
        holes, img_h_for_bands, n_bands=n_bands, min_holes=min_holes_per_band,
        boundary_tol_px=boundary_tol_px,
    )
    # Keep full pattern bounds for overlay, but use a trimmed copy for numeric
    # verticality metrics. Top/bottom bands are shown because they matter to
    # the operator, yet they are often sparse and can overdrive zigzag numbers.
    pat_left_metric = _trim_y_extremes(pat_left)
    pat_right_metric = _trim_y_extremes(pat_right)

    pattern_left_x  = float(min(hh.x - hh.r for hh in holes))
    pattern_right_x = float(max(hh.x + hh.r for hh in holes))

    left_margin_px  = pattern_left_x  - left_x
    right_margin_px = right_x - pattern_right_x
    margin_delta_px = left_margin_px  - right_margin_px
    offset_px       = margin_delta_px / 2.0
    within_tol      = (tol_px <= 0.0) or (abs(offset_px) <= tol_px)

    # Per-band margin statistics
    band_lm: list[float] = []
    band_rm: list[float] = []
    for i in range(n_bands):
        if i in edge_left and i in edge_right and i in pat_left_metric and i in pat_right_metric:
            band_lm.append(pat_left_metric[i][0]  - edge_left[i][0])
            band_rm.append(edge_right[i][0] - pat_right_metric[i][0])

    left_margin_std  = float(np.std(band_lm)) if len(band_lm) >= 2 else 0.0
    right_margin_std = float(np.std(band_rm)) if len(band_rm) >= 2 else 0.0

    # Per-band point tuples for overlay (all ROI-relative, raw — not smoothed)
    left_edge_points  = tuple(sorted(edge_left.values(),  key=lambda p: p[1]))
    right_edge_points = tuple(sorted(edge_right.values(), key=lambda p: p[1]))
    pattern_left_points  = tuple(sorted(pat_left.values(),  key=lambda p: p[1]))
    pattern_right_points = tuple(sorted(pat_right.values(), key=lambda p: p[1]))

    def _slope_deg(pts: list) -> float:
        """Slope angle from vertical (°). 0 = vertical. Sign: + leans right going down."""
        c = _fit_line_robust(pts)
        return float(np.degrees(np.arctan(c[0]))) if c is not None else 0.0

    # Pendiente de chapa: solo bandas CENTRALES (60% del alto) y con rampa de
    # transición angosta. El borde real es un arco (distorsión de lente) y las
    # puntas curvas inclinan la recta según qué bandas entraron; las bandas de
    # rampa ancha (borde lavado por la luz) tienen el cruce corrido unos px y
    # tuercen la pendiente, inflando pattern_sheet_slope_delta sin
    # desalineación real del patrón.
    def _chapa_slope_pts(
        d: dict[int, tuple[float, float]],
        ramps: dict[int, float],
    ) -> list[tuple[float, float]]:
        central = _trim_y_extremes(d, trim_frac=0.20)
        if len(ramps) >= 4:
            med_w = float(np.median(list(ramps.values())))
            sharp = {
                k: v for k, v in central.items()
                if ramps.get(k, med_w) <= max(med_w * 2.0, med_w + 10.0)
            }
            if len(sharp) >= 4:
                central = sharp
        return list(central.values())

    _chapa_left_central = _chapa_slope_pts(edge_left, left_ramp)
    _chapa_right_central = _chapa_slope_pts(edge_right, right_ramp)
    left_edge_slope_deg = _slope_deg(
        _chapa_left_central if len(_chapa_left_central) >= 4 else left_pts_list
    )
    right_edge_slope_deg = _slope_deg(
        _chapa_right_central if len(_chapa_right_central) >= 4 else right_pts_list
    )
    pattern_left_slope_deg  = _slope_deg(list(pat_left_metric.values()))
    pattern_right_slope_deg = _slope_deg(list(pat_right_metric.values()))
    pattern_sheet_slope_delta_left_deg = pattern_left_slope_deg - left_edge_slope_deg
    pattern_sheet_slope_delta_right_deg = pattern_right_slope_deg - right_edge_slope_deg
    pattern_sheet_slope_delta_max_deg = max(
        abs(pattern_sheet_slope_delta_left_deg),
        abs(pattern_sheet_slope_delta_right_deg),
    )

    def _zigzag_residuals(pts_lists: list) -> tuple[float, float]:
        """Compute (std_px, max_px) of horizontal residuals from fitted line. Returns (0,0) if insufficient data."""
        residuals: list[float] = []
        for pts in pts_lists:
            if len(pts) < 3:
                continue
            c = _fit_line_robust(list(pts))
            if c is None:
                continue
            xs = np.array([p[0] for p in pts])
            ys = np.array([p[1] for p in pts])
            residuals.extend(np.abs(xs - (c[0] * ys + c[1])).tolist())
        if residuals:
            arr = np.array(residuals)
            return float(arr.std()), float(arr.max())
        return 0.0, 0.0

    # CHAPA (sheet edge) zigzag — uses raw points; outliers here ARE the signal (vibration)
    chapa_zigzag_std_px, chapa_zigzag_max_px = _zigzag_residuals(
        [left_pts_list, right_pts_list]
    )

    # PATRON (hole pattern edge) zigzag — smooth before computing to suppress sparse-band outliers
    pat_left_pts_s  = _smooth_points_x(
        sorted(pat_left_metric.values(), key=lambda p: p[1]), smooth_window
    )
    pat_right_pts_s = _smooth_points_x(
        sorted(pat_right_metric.values(), key=lambda p: p[1]), smooth_window
    )
    pattern_zigzag_std_px, pattern_zigzag_max_px = _zigzag_residuals(
        [pat_left_pts_s, pat_right_pts_s]
    )

    # PATRON CENTER zigzag — smooth before computing; catches internal lateral waviness
    center_pts = _pattern_center_by_band(pat_left_metric, pat_right_metric)
    center_pts_s = _smooth_points_x(center_pts, smooth_window)
    pattern_center_zigzag_std_px, pattern_center_zigzag_max_px = _zigzag_residuals(
        [center_pts_s]
    )

    # Real per-band center polylines for overlay drawing.
    # Sheet center: midpoint between left/right CHAPA edges per band.
    # Pattern center: midpoint between left/right PATRON bounds per band (full, not metric).
    sheet_center_pts = sorted([
        ((edge_left[i][0] + edge_right[i][0]) / 2.0,
         (edge_left[i][1] + edge_right[i][1]) / 2.0)
        for i in sorted(set(edge_left) & set(edge_right))
    ], key=lambda p: p[1])
    pattern_center_pts_full = _pattern_center_by_band(pat_left, pat_right)

    return CenteringResult(
        left_x=left_x,
        right_x=right_x,
        sheet_center_x=sheet_center_x,
        holes_center_x=holes_center_x,
        offset_px=offset_px,
        sheet_width_px=right_x - left_x,
        within_tol=within_tol,
        pattern_left_x=pattern_left_x,
        pattern_right_x=pattern_right_x,
        left_margin_px=left_margin_px,
        right_margin_px=right_margin_px,
        margin_delta_px=margin_delta_px,
        left_edge_points=left_edge_points,
        right_edge_points=right_edge_points,
        pattern_left_points=pattern_left_points,
        pattern_right_points=pattern_right_points,
        left_margin_std=left_margin_std,
        right_margin_std=right_margin_std,
        centering_reliable=centering_reliable,
        left_edge_slope_deg=left_edge_slope_deg,
        right_edge_slope_deg=right_edge_slope_deg,
        pattern_left_slope_deg=pattern_left_slope_deg,
        pattern_right_slope_deg=pattern_right_slope_deg,
        chapa_zigzag_std_px=chapa_zigzag_std_px,
        chapa_zigzag_max_px=chapa_zigzag_max_px,
        pattern_zigzag_std_px=pattern_zigzag_std_px,
        pattern_zigzag_max_px=pattern_zigzag_max_px,
        pattern_center_zigzag_std_px=pattern_center_zigzag_std_px,
        pattern_center_zigzag_max_px=pattern_center_zigzag_max_px,
        pattern_sheet_slope_delta_left_deg=pattern_sheet_slope_delta_left_deg,
        pattern_sheet_slope_delta_right_deg=pattern_sheet_slope_delta_right_deg,
        pattern_sheet_slope_delta_max_deg=pattern_sheet_slope_delta_max_deg,
        sheet_center_points=tuple(sheet_center_pts),
        pattern_center_points=tuple(pattern_center_pts_full),
    )
