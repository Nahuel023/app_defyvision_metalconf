from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np

from src.io.load_images import load_bgr_image
from src.io.save_results import save_image
from src.patterns.pattern_io import load_pattern, find_pattern_path, Pattern
from src.patterns.roi import apply_roi, load_roi, ROI
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.annotate import draw_compare_overlay, draw_centering_overlay, draw_machine_stop_badge
from src.pipeline.machine_stop import MachineStopDetector
from src.pipeline.compare import CompareReport, compare_missing_only
from src.pipeline.detect_holes import Hole, detect_holes_from_mask
from src.pipeline.edge_centering import CenteringResult, compute_centering
from src.pipeline.grid_fitting import grid_compare_points
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class InspectionResult:
    model: str
    image_path: Path
    status: str
    report: CompareReport
    holes: list[Hole]
    mask: np.ndarray
    overlay: np.ndarray
    angle_deg: float
    used_lines: int
    shift_xy: tuple[float, float] | None
    detection_ratio: float = 1.0
    alignment_ok: bool = True
    centering: CenteringResult | None = None
    centering_nok: bool = False   # True cuando el NOK fue causado (o agravado) por descentrado
    capture_quality_degraded: bool = False  # True cuando ratio cae bajo quality_ratio_min (no afecta NOK)
    blur_score: float = 0.0        # Varianza del Laplaciano — mayor es más nítido
    frame_quality: str = "GOOD"    # "GOOD" | "LOW_QUALITY"
    machine_stop: bool = False          # True cuando una zona de agujeros faltantes supera el umbral
    frame_geometry_quality: str = "STABLE"   # "STABLE" | "UNSTABLE" (CHAPA zigzag excesivo)
    pattern_alignment_warn: bool = False     # True cuando el PATRON zigzaguea (desalineado)
    chapa_zigzag_std_px: float = 0.0
    chapa_zigzag_max_px: float = 0.0
    pattern_zigzag_std_px: float = 0.0
    pattern_zigzag_max_px: float = 0.0
    pattern_center_zigzag_std_px: float = 0.0
    pattern_center_zigzag_max_px: float = 0.0


@dataclass(frozen=True)
class FolderInspectionSummary:
    model: str
    input_dir: Path
    total: int
    ok: int
    nok: int
    uncertain: int
    results: list[InspectionResult]
    temporal_ok: int
    temporal_nok: int
    temporal_results: list["TemporalFrameResult"]
    consecutive_nok_frames: int
    frame_rate_hz: float
    max_response_sec: float
    response_time_sec: float
    meets_response_target: bool
    low_quality: int = 0
    max_low_quality_streak: int = 0
    machine_stop_count: int = 0


@dataclass(frozen=True)
class TemporalFrameResult:
    result: InspectionResult
    nok_streak: int
    decision_status: str
    triggered: bool
    low_quality_streak: int = 0


def iter_image_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            yield path


def inspect_image(
    model: str,
    img_path: Path,
    save: bool = False,
    scanner_id: str | None = None,
    _machine_stop_detector: "MachineStopDetector | None" = None,
    _preloaded: Optional[dict] = None,
) -> InspectionResult:
    """Inspect an image from disk."""
    img_full = load_bgr_image(img_path)
    result = _inspect_bgr(model, img_full, image_path=img_path, scanner_id=scanner_id,
                          _machine_stop_detector=_machine_stop_detector,
                          _preloaded=_preloaded)
    if save:
        _save_result_images(result)
    return result


def inspect_frame(
    model: str,
    frame: np.ndarray,
    frame_id: str = "live",
    save: bool = False,
    scanner_id: str | None = None,
    _preloaded: Optional[dict] = None,
) -> InspectionResult:
    """Inspect a BGR frame captured from a live camera (no disk read)."""
    result = _inspect_bgr(model, frame, image_path=Path(frame_id),
                          scanner_id=scanner_id, _preloaded=_preloaded)
    if save:
        _save_result_images(result)
    return result


def _inspect_bgr(
    model: str,
    img_full: np.ndarray,
    image_path: Path,
    scanner_id: str | None = None,
    _preloaded: Optional[dict] = None,
    _machine_stop_detector: "MachineStopDetector | None" = None,
) -> InspectionResult:
    """Core inspection logic on a pre-loaded BGR frame.

    _preloaded: optional dict with pre-cached keys 'tolerances', 'pattern', 'roi',
    'ema_state'. Provided by Inspector to avoid per-frame disk I/O.
    """
    pre = _preloaded or {}

    # Machine stop detector: explicit param takes precedence over _preloaded
    _ms_detector: MachineStopDetector | None = (
        _machine_stop_detector or pre.get("machine_stop_detector")
    )

    tolerances = pre.get("tolerances") or load_tolerances(model)
    pattern: Pattern = pre.get("pattern") or load_pattern(find_pattern_path(model, scanner_id))
    roi: Optional[ROI] = pre.get("roi", _SENTINEL)
    if roi is _SENTINEL:
        roi = load_roi(model, scanner_id)
    ema_state: Optional[dict] = pre.get("ema_state")

    threshold        = int(tolerances["threshold"])
    use_channel      = str(tolerances["use_channel"])
    polarity         = str(tolerances["polarity"])
    min_area         = float(tolerances["min_area"])
    max_area_raw     = tolerances.get("max_area")
    max_area         = float(max_area_raw) if max_area_raw is not None else None
    circularity_min  = float(tolerances["circularity_min"])
    tol_xy_px        = float(tolerances["tol_xy_px"])
    aspect_ratio_max = float(tolerances["aspect_ratio_max"])
    align_match_tol_px = float(tolerances["align_match_tol_px"])
    min_match_count  = int(tolerances["min_match_count"])
    use_clahe           = bool(tolerances.get("use_clahe", False))
    clahe_clip          = float(tolerances.get("clahe_clip", 2.0))
    clahe_tile          = int(tolerances.get("clahe_tile", 8))
    use_otsu            = bool(tolerances.get("use_otsu", False))
    blur_ksize          = int(tolerances.get("blur_ksize", 5))
    open_ksize          = int(tolerances.get("open_ksize", 3))
    close_ksize         = int(tolerances.get("close_ksize", 5))
    edge_margin_px        = float(tolerances.get("edge_margin_px", 0.0))
    grid_max_missing      = int(tolerances.get("grid_max_missing", 0))
    min_detection_ratio   = float(tolerances.get("min_detection_ratio", 0.30))
    quality_ratio_min     = float(tolerances.get("quality_ratio_min", 0.0))
    max_extra             = int(tolerances.get("max_extra", -1))
    bbox_filter_margin_px = float(tolerances.get("bbox_filter_margin_px", 0.0))
    grid_affine_refinement = bool(tolerances.get("grid_affine_refinement", False))
    blur_score_min = float(tolerances.get("blur_score_min", 0.0))
    low_quality_max_streak = int(tolerances.get("low_quality_max_streak", 10))  # noqa: F841
    extra_min_dist_factor = float(tolerances.get("extra_min_dist_factor", 0.0))
    use_hungarian_matching = bool(tolerances.get("use_hungarian_matching", False))
    # CHAPA edge zigzag → IMAGEN INESTABLE (frame quality issue, skip decisions)
    verticality_quality_enabled = bool(tolerances.get("verticality_quality_enabled", False))
    chapa_zigzag_std_max_px = float(
        tolerances.get("chapa_zigzag_std_max_px",
                       tolerances.get("pattern_zigzag_std_max_px", 4.0))  # backward compat
    )
    chapa_zigzag_abs_max_px = float(
        tolerances.get("chapa_zigzag_abs_max_px",
                       tolerances.get("pattern_zigzag_abs_max_px", 10.0))
    )
    # PATRON edge zigzag → DETENER MAQUINA (mechanical misalignment, does not skip decisions)
    pattern_align_enabled     = bool(tolerances.get("pattern_align_enabled", False))
    pattern_align_std_max_px  = float(tolerances.get("pattern_align_std_max_px", 6.0))
    pattern_align_abs_max_px  = float(tolerances.get("pattern_align_abs_max_px", 15.0))
    # PATRON CENTER zigzag → same consequence as edge zigzag (finer internal misalignment)
    pattern_center_align_enabled    = bool(tolerances.get("pattern_center_align_enabled", False))
    pattern_center_zigzag_std_max   = float(tolerances.get("pattern_center_zigzag_std_max_px", 8.0))
    pattern_center_zigzag_abs_max   = float(tolerances.get("pattern_center_zigzag_abs_max_px", 18.0))

    img_aligned, align_res = align_image_by_right_edge(img_full, ema_state=ema_state)

    img = apply_roi(img_aligned, roi) if roi is not None else img_aligned

    preprocess_kw = dict(
        threshold=threshold, use_channel=use_channel, polarity=polarity,
        use_clahe=use_clahe, clahe_clip=clahe_clip, clahe_tile=clahe_tile,
        use_otsu=use_otsu,
        blur_ksize=blur_ksize, open_ksize=open_ksize, close_ksize=close_ksize,
    )
    detect_kw = dict(
        min_area=min_area, max_area=max_area,
        circularity_min=circularity_min,
        aspect_ratio_max=aspect_ratio_max,
        edge_margin_px=edge_margin_px,
    )

    mask  = preprocess_for_holes(img, **preprocess_kw)
    holes = detect_holes_from_mask(mask, **detect_kw)
    detected_points = [(h.x, h.y) for h in holes]

    # Blur score: Laplacian variance on the inspection ROI.
    # Skip entirely when blur_score_min == 0 (not configured) to save ~3ms/frame.
    if blur_score_min > 0.0:
        _gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_score = float(cv2.Laplacian(_gray, cv2.CV_64F).var())
        frame_quality = "LOW_QUALITY" if blur_score < blur_score_min else "GOOD"
    else:
        blur_score = 0.0
        frame_quality = "GOOD"

    img_h, img_w = img.shape[:2]

    shift_xy: tuple[float, float] | None = None
    alignment_ok = True

    n_expected_total = len(pattern.points)

    compare_cells: list[tuple[int, int]] = []

    if pattern.has_grid and detected_points:
        detected_arr = np.array(detected_points, dtype=np.float32)
        tol_affine = tol_xy_px * 1.5 if grid_affine_refinement else 0.0
        compare_points, compare_cells = grid_compare_points(
            detected_arr,
            pattern.cells,
            pattern.dx,
            pattern.dy,
            pattern.phase_x,
            pattern.phase_y,
            None,
            img_w, img_h,
            edge_margin_px,
            tol_affine=tol_affine,
        )
    else:
        transform = _estimate_alignment_transform(
            pattern.points, holes,
            match_tol_px=align_match_tol_px, min_match_count=min_match_count,
        )
        if transform is not None:
            shift_xy = (float(transform[0, 2]), float(transform[1, 2]))
            inv = cv2.invertAffineTransform(transform)
            pat_arr = np.array(pattern.points, dtype=np.float32).reshape(-1, 1, 2)
            projected = cv2.transform(pat_arr, inv).reshape(-1, 2)
            compare_points = [
                (float(ex), float(ey)) for ex, ey in projected
                if edge_margin_px <= ex <= img_w - edge_margin_px
                and edge_margin_px <= ey <= img_h - edge_margin_px
            ]
        else:
            alignment_ok = False
            compare_points = [
                (px, py) for px, py in pattern.points
                if edge_margin_px <= px <= img_w - edge_margin_px
                and edge_margin_px <= py <= img_h - edge_margin_px
            ]

    # Recortar posiciones esperadas al rango Y de agujeros detectados + margen dy.
    # Evita contar como "missing" las filas del patrón que salen de cuadro cuando
    # el borde de la zona perforada cruza el encuadre (corte de patrón).
    # compare_cells se mantiene sincronizado para que la identidad de celda sea
    # correcta tras el recorte.
    if compare_points and detected_points and pattern.has_grid:
        det_ys = [y for _x, y in detected_points]
        dy_clip = float(pattern.dy) * 1.5
        y_clip_min = min(det_ys) - dy_clip
        y_clip_max = max(det_ys) + dy_clip
        if compare_cells:
            _filtered = [
                (p, c) for p, c in zip(compare_points, compare_cells)
                if y_clip_min <= p[1] <= y_clip_max
            ]
            compare_points = [p for p, _ in _filtered]
            compare_cells  = [c for _, c in _filtered]
        else:
            compare_points = [
                (x, y) for x, y in compare_points
                if y_clip_min <= y <= y_clip_max
            ]

    # Filtrar detecciones al bounding box del patrón esperado para eliminar agujeros
    # reales del material fuera de la ventana del patrón (reducen extra y costo de matching).
    # Los holes originales se mantienen intactos para el cálculo de centrado.
    if compare_points and bbox_filter_margin_px >= 0:
        xs = [p[0] for p in compare_points]
        ys = [p[1] for p in compare_points]
        m = bbox_filter_margin_px
        bx1, bx2 = min(xs) - m, max(xs) + m
        by1, by2 = min(ys) - m, max(ys) + m
        detected_in_bbox = [(x, y) for x, y in detected_points
                            if bx1 <= x <= bx2 and by1 <= y <= by2]
    else:
        detected_in_bbox = detected_points

    _max_missing = grid_max_missing if (pattern.has_grid and detected_points) else 0
    report  = compare_missing_only(compare_points, detected_in_bbox,
                                   tol_xy_px=tol_xy_px, max_missing=_max_missing,
                                   max_extra=max_extra,
                                   extra_min_dist_factor=extra_min_dist_factor,
                                   expected_cells=compare_cells if compare_cells else None,
                                   use_hungarian=use_hungarian_matching)

    detection_ratio = len(holes) / n_expected_total if n_expected_total > 0 else 1.0
    capture_quality_degraded = (
        quality_ratio_min > 0.0
        and detection_ratio < quality_ratio_min
        and detection_ratio >= min_detection_ratio
    )

    # Centering check: measure lateral offset of holes relative to sheet edges.
    # Pass the full-frame aligned image so search windows can see the backlights
    # (which are outside the ROI crop and would be invisible in `img`).
    center_offset_tol_px = float(tolerances.get("center_offset_tol_px", 0.0))
    centering = compute_centering(img_aligned, holes, roi=roi, tol_px=center_offset_tol_px)
    centering_nok = (
        centering is not None
        and not centering.within_tol
        and center_offset_tol_px > 0
    )
    final_status = "NOK" if (report.status == "NOK" or centering_nok) else report.status

    # --- Frame geometry quality ---
    # CHAPA zigzag: high → IMAGEN INESTABLE (camera/sheet vibration) → skip all decisions.
    # PATRON zigzag: high → DETENER MAQUINA - patron desalineado (mechanical issue).
    chapa_zigzag_std_px = 0.0
    chapa_zigzag_max_px = 0.0
    pattern_zigzag_std_px = 0.0
    pattern_zigzag_max_px = 0.0
    pattern_center_zigzag_std_px = 0.0
    pattern_center_zigzag_max_px = 0.0
    frame_geometry_quality = "STABLE"
    pattern_alignment_warn = False

    if centering is not None:
        chapa_zigzag_std_px          = getattr(centering, "chapa_zigzag_std_px",          0.0)
        chapa_zigzag_max_px          = getattr(centering, "chapa_zigzag_max_px",          0.0)
        pattern_zigzag_std_px        = getattr(centering, "pattern_zigzag_std_px",        0.0)
        pattern_zigzag_max_px        = getattr(centering, "pattern_zigzag_max_px",        0.0)
        pattern_center_zigzag_std_px = getattr(centering, "pattern_center_zigzag_std_px", 0.0)
        pattern_center_zigzag_max_px = getattr(centering, "pattern_center_zigzag_max_px", 0.0)

        if verticality_quality_enabled:
            if (chapa_zigzag_std_px > chapa_zigzag_std_max_px
                    or chapa_zigzag_max_px > chapa_zigzag_abs_max_px):
                frame_geometry_quality = "UNSTABLE"
                frame_quality = "LOW_QUALITY"  # skip streaks + machine stop

        if pattern_align_enabled and frame_geometry_quality != "UNSTABLE":
            if (pattern_zigzag_std_px > pattern_align_std_max_px
                    or pattern_zigzag_max_px > pattern_align_abs_max_px):
                pattern_alignment_warn = True
                final_status = "NOK"   # desalineamiento mecánico del patron → NOK

        if pattern_center_align_enabled and frame_geometry_quality != "UNSTABLE":
            if (pattern_center_zigzag_std_px > pattern_center_zigzag_std_max
                    or pattern_center_zigzag_max_px > pattern_center_zigzag_abs_max):
                pattern_alignment_warn = True
                final_status = "NOK"   # zigzag interno del patrón → NOK

    # Near-miss pairs: missing expected points with a detected hole between tol and 2×tol.
    # Shown as thin cyan lines in the overlay so the operator can see the gap at a glance.
    near_miss_pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if report.missing_points and detected_in_bbox:
        _det_arr = np.array(detected_in_bbox, dtype=np.float32)
        _mis_arr = np.array(report.missing_points, dtype=np.float32)
        _diff    = _mis_arr[:, None, :] - _det_arr[None, :, :]
        _dists   = np.sqrt((_diff ** 2).sum(axis=2))   # (n_mis, n_det)
        _near_d  = _dists.min(axis=1)
        _near_j  = _dists.argmin(axis=1)
        for _i, (_d, _j) in enumerate(zip(_near_d, _near_j)):
            if tol_xy_px < _d <= tol_xy_px * 2.0:
                near_miss_pairs.append(
                    (report.missing_points[_i], detected_in_bbox[_j])
                )

    # Machine stop detection: track persistent missing holes across frames.
    # Pass missing_cells so grid-mode tracking can key by column ci instead
    # of pixel X — the same broken punch is then recognised across frames
    # even as the sheet advances vertically.
    machine_stop = False
    _ms_reason   = "AGUJERO PERSISTENTE FALTANTE"
    if _ms_detector is not None:
        machine_stop, _ms_positions = _ms_detector.update(
            report.missing_points, near_miss_pairs, frame_quality, img_h,
            missing_cells=report.missing_cells if report.missing_cells else None,
        )
        if machine_stop:
            cols = _ms_detector.triggered_columns
            if cols:
                col_str  = ", ".join(str(c) for c in cols)
                _ms_reason = f"AGUJERO FALTANTE PERSISTENTE EN COLUMNA {col_str}"
    if machine_stop:
        final_status = "NOK"

    # Draw hole annotations on the ROI image (hole coords are in ROI space)
    overlay_roi = draw_compare_overlay(img, holes, report.missing_points, final_status,
                                       extra_points=report.extra_points,
                                       near_miss_pairs=near_miss_pairs)
    overlay_roi = _draw_warnings(overlay_roi, detection_ratio, alignment_ok,
                                 min_detection_ratio, capture_quality_degraded,
                                 frame_quality, frame_geometry_quality)

    # Composite annotated ROI onto the full aligned frame so the overlay shows
    # the complete image without any crop
    overlay = img_aligned.copy()
    if roi is not None:
        overlay[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w] = overlay_roi
    else:
        overlay = overlay_roi

    # Draw centering overlay on the FULL-FRAME image so CHAPA edge lines land on
    # the real sheet edges (which are outside the ROI crop in ROI-relative coords).
    # roi_x/roi_y convert CenteringResult ROI-relative coords → full-frame coords.
    if centering is not None:
        _roi_x = roi.x if roi is not None else 0
        _roi_y = roi.y if roi is not None else 0
        overlay = draw_centering_overlay(
            overlay, centering, tag_nok=centering_nok,
            roi_x=_roi_x, roi_y=_roi_y,
            pattern_warn=pattern_alignment_warn,
        )

    if machine_stop and pattern_alignment_warn:
        overlay = draw_machine_stop_badge(overlay, reason=_ms_reason, index=0)
        overlay = draw_machine_stop_badge(overlay, reason="PATRON DESALINEADO", index=1)
    elif machine_stop:
        overlay = draw_machine_stop_badge(overlay, reason=_ms_reason, index=0)
    elif pattern_alignment_warn:
        overlay = draw_machine_stop_badge(overlay, reason="PATRON DESALINEADO", index=0)

    return InspectionResult(
        model=model,
        image_path=image_path,
        status=final_status,
        report=report,
        holes=holes,
        mask=mask,
        overlay=overlay,
        angle_deg=float(align_res.angle_deg),
        used_lines=int(align_res.used_lines),
        shift_xy=shift_xy,
        detection_ratio=detection_ratio,
        alignment_ok=alignment_ok,
        centering=centering,
        centering_nok=centering_nok,
        capture_quality_degraded=capture_quality_degraded,
        blur_score=blur_score,
        frame_quality=frame_quality,
        machine_stop=machine_stop,
        frame_geometry_quality=frame_geometry_quality,
        pattern_alignment_warn=pattern_alignment_warn,
        chapa_zigzag_std_px=chapa_zigzag_std_px,
        chapa_zigzag_max_px=chapa_zigzag_max_px,
        pattern_zigzag_std_px=pattern_zigzag_std_px,
        pattern_zigzag_max_px=pattern_zigzag_max_px,
        pattern_center_zigzag_std_px=pattern_center_zigzag_std_px,
        pattern_center_zigzag_max_px=pattern_center_zigzag_max_px,
    )


def _draw_warnings(
    overlay: np.ndarray,
    detection_ratio: float,
    alignment_ok: bool,
    min_detection_ratio: float,
    capture_quality_degraded: bool = False,
    frame_quality: str = "GOOD",
    frame_geometry_quality: str = "STABLE",
) -> np.ndarray:
    warnings = []
    if detection_ratio < min_detection_ratio:
        warnings.append(f"DETECCION BAJA ({detection_ratio:.0%})")
    elif capture_quality_degraded:
        warnings.append(f"CALIDAD DEGRADADA ({detection_ratio:.0%})")
    if not alignment_ok:
        warnings.append("ALIGN FALLBACK")
    if frame_geometry_quality == "UNSTABLE":
        warnings.append("IMAGEN INESTABLE - NO DECIDE")
    elif frame_quality == "LOW_QUALITY":
        warnings.append("CALIDAD BAJA")
    if not warnings:
        return overlay
    out = overlay.copy()
    h = out.shape[0]
    for i, msg in enumerate(warnings):
        y = h - 15 - i * 35
        cv2.putText(out, msg, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (0, 200, 255), 2, cv2.LINE_AA)
    return out


# Sentinel para distinguir roi=None (no hay ROI) de roi no provisto en _preloaded
_SENTINEL = object()


def inspect_folder(
    model: str,
    input_dir: Path,
    save: bool = False,
    frame_rate_hz: float | None = None,
    consecutive_nok_frames: int | None = None,
    max_response_sec: float | None = None,
    scanner_id: str | None = None,
) -> FolderInspectionSummary:
    tolerances = load_tolerances(model)
    frame_rate_hz = float(
        tolerances["frame_rate_hz"] if frame_rate_hz is None else frame_rate_hz
    )
    consecutive_nok_frames = int(
        tolerances["consecutive_nok_frames"]
        if consecutive_nok_frames is None
        else consecutive_nok_frames
    )
    max_response_sec = float(
        tolerances["max_response_sec"] if max_response_sec is None else max_response_sec
    )

    low_quality_max_streak = int(tolerances.get("low_quality_max_streak", 10))

    ms_detector = MachineStopDetector(
        enabled=bool(tolerances.get("machine_stop_enabled", False)),
        missing_frames=int(tolerances.get("machine_stop_missing_frames", 5)),
        min_missing=int(tolerances.get("machine_stop_min_missing", 1)),
        same_zone_px=float(tolerances.get("machine_stop_same_zone_px", 35.0)),
        ignore_near_miss=bool(tolerances.get("machine_stop_ignore_near_miss", True)),
        track_by_grid=bool(tolerances.get("machine_stop_track_by_grid", True)),
        same_column_tol_cells=int(tolerances.get("machine_stop_same_column_tol_cells", 0)),
    )

    image_paths = list(iter_image_files(input_dir))
    results = [inspect_image(model, path, save=save, scanner_id=scanner_id,
                             _machine_stop_detector=ms_detector)
               for path in image_paths]
    ok_count = sum(1 for result in results if result.status == "OK")
    low_quality_count = sum(
        1 for r in results if getattr(r, "frame_quality", "GOOD") == "LOW_QUALITY"
    )
    machine_stop_count = sum(1 for r in results if r.machine_stop)
    temporal_results = _apply_temporal_rule(
        results, consecutive_nok_frames, low_quality_max_streak
    )
    temporal_ok = sum(1 for item in temporal_results if item.decision_status == "OK")
    max_lq_streak = max(
        (item.low_quality_streak for item in temporal_results), default=0
    )
    response_time_sec = (
        float("inf") if frame_rate_hz <= 0 else consecutive_nok_frames / frame_rate_hz
    )
    return FolderInspectionSummary(
        model=model,
        input_dir=input_dir,
        total=len(results),
        ok=ok_count,
        nok=len(results) - ok_count,
        uncertain=0,
        results=results,
        temporal_ok=temporal_ok,
        temporal_nok=len(temporal_results) - temporal_ok,
        temporal_results=temporal_results,
        consecutive_nok_frames=consecutive_nok_frames,
        frame_rate_hz=frame_rate_hz,
        max_response_sec=max_response_sec,
        response_time_sec=response_time_sec,
        meets_response_target=response_time_sec <= max_response_sec,
        low_quality=low_quality_count,
        max_low_quality_streak=max_lq_streak,
        machine_stop_count=machine_stop_count,
    )


def save_result_images(result: InspectionResult) -> None:
    """Guarda overlay en data/output/ok|nok y máscara en data/output/debug."""
    out_dir = Path("data/output/ok") if result.status == "OK" else Path("data/output/nok")
    dbg_dir = Path("data/output/debug")
    save_image(dbg_dir / f"{result.image_path.stem}_mask.png", result.mask)
    save_image(out_dir  / f"{result.image_path.stem}_overlay.png", result.overlay)


# Alias privado para compatibilidad interna
_save_result_images = save_result_images


def _estimate_alignment_transform(
    pattern_points: list[tuple[float, float]],
    detected_holes: list[Hole],
    match_tol_px: float,
    min_match_count: int,
) -> np.ndarray | None:
    if len(pattern_points) < min_match_count or len(detected_holes) < min_match_count:
        return None

    det_np = np.array([(h.x, h.y) for h in detected_holes], dtype=np.float32)
    pat_np = np.array(pattern_points, dtype=np.float32)

    # Voting histogram para estimar pre-shift dominante
    _bin = 12.0
    _verify_tol = 20.0
    diffs = pat_np[:, None, :] - det_np[None, :, :]   # (n_pat, n_det, 2)
    bx = np.round(diffs[:, :, 0] / _bin).astype(np.int32).ravel()
    by = np.round(diffs[:, :, 1] / _bin).astype(np.int32).ravel()
    votes: dict[tuple[int, int], int] = {}
    for kx, ky in zip(bx.tolist(), by.tolist()):
        key = (kx, ky)
        votes[key] = votes.get(key, 0) + 1
    candidates: list[np.ndarray] = [
        np.array([kx * _bin, ky * _bin], dtype=np.float32)
        for (kx, ky), _ in sorted(votes.items(), key=lambda x: -x[1])[:8]
    ]
    candidates.append((pat_np.mean(axis=0) - det_np.mean(axis=0)).astype(np.float32))

    # Verificación vectorizada de inliers — una matriz por candidato en lugar de loop Python
    best_inliers = -1
    pre_shift = np.zeros(2, dtype=np.float32)
    _verify_tol2 = _verify_tol * _verify_tol
    for cand in candidates:
        shifted = det_np + cand                                    # (n_det, 2)
        diff_v  = shifted[:, None, :] - pat_np[None, :, :]        # (n_det, n_pat, 2)
        min_d2  = (diff_v * diff_v).sum(axis=2).min(axis=1)       # (n_det,)
        inliers = int((min_d2 < _verify_tol2).sum())
        if inliers > best_inliers:
            best_inliers = inliers
            pre_shift = cand
    shifted_det = det_np + pre_shift

    src_points: list[np.ndarray] = []
    dst_points: list[np.ndarray] = []
    used_pat_idx: set[int] = set()

    for det_raw, det_shifted in zip(det_np, shifted_det):
        distances = np.linalg.norm(pat_np - det_shifted, axis=1)
        best_idx = int(np.argmin(distances))
        if distances[best_idx] > match_tol_px or best_idx in used_pat_idx:
            continue
        used_pat_idx.add(best_idx)
        src_points.append(det_raw)
        dst_points.append(pat_np[best_idx])

    if len(src_points) < min_match_count:
        return None

    affine, _ = cv2.estimateAffinePartial2D(
        np.array(src_points, dtype=np.float32),
        np.array(dst_points, dtype=np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=max(3.0, match_tol_px * 0.25),
        maxIters=500,       # 2000 → 500: suficiente con 99% confianza para puntos bien matcheados
        confidence=0.99,
    )
    return affine


def _apply_temporal_rule(
    results: list[InspectionResult],
    consecutive_nok_frames: int,
    low_quality_max_streak: int = 10,
) -> list[TemporalFrameResult]:
    streak = 0
    lq_streak = 0
    temporal_results: list[TemporalFrameResult] = []
    for result in results:
        quality = getattr(result, "frame_quality", "GOOD")
        if quality == "LOW_QUALITY":
            lq_streak += 1
            # If too many low-quality frames in a row, reset streak to avoid
            # permanently blocking FAULT detection when sensor/backlight degrades.
            if low_quality_max_streak > 0 and lq_streak >= low_quality_max_streak:
                streak = 0
                lq_streak = 0
            # else: hold — don't increment, don't reset nok streak
        else:
            lq_streak = 0
            if result.status == "NOK":
                streak += 1
            else:
                streak = 0

        decision_status = (
            "NOK"
            if getattr(result, "machine_stop", False) or streak >= consecutive_nok_frames
            else "OK"
        )
        temporal_results.append(
            TemporalFrameResult(
                result=result,
                nok_streak=streak,
                low_quality_streak=lq_streak,
                decision_status=decision_status,
                triggered=decision_status == "NOK",
            )
        )
    return temporal_results
