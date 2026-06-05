import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np

from src.io.load_images import load_bgr_image
from src.io.save_results import save_image
from src.patterns.pattern_io import load_pattern, find_pattern_path, Pattern
from src.patterns.roi import apply_roi, load_roi, ROI
from src.pipeline.align_edge import EdgeAlignResult, align_image_by_right_edge
from src.pipeline.annotate import draw_compare_overlay, draw_centering_overlay, draw_machine_stop_badge, draw_status_indicator, draw_tilt_indicator, draw_blur_indicator
from src.pipeline.machine_stop import MachineStopDetector
from src.pipeline.compare import CompareReport, compare_missing_only
from src.pipeline.detect_holes import Hole, detect_holes_from_mask
from src.pipeline.edge_centering import CenteringResult, compute_centering
from src.pipeline.grid_fitting import grid_compare_points, estimate_lattice_tilt_deg, rotate_points
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def _filter_points_by_pattern_hull(
    detected_points: list[tuple[float, float]],
    compare_points: list[tuple[float, float]],
    margin_px: float,
    detected_types: list[str] | None = None,
) -> tuple[list[tuple[float, float]], list[str] | None]:
    """Keep detections inside or near the convex hull of the expected pattern."""
    if margin_px <= 0.0 or len(compare_points) < 3 or not detected_points:
        return detected_points, detected_types

    hull = cv2.convexHull(np.array(compare_points, dtype=np.float32).reshape(-1, 1, 2))
    kept_points: list[tuple[float, float]] = []
    kept_types: list[str] = []
    for idx, pt in enumerate(detected_points):
        signed_dist = cv2.pointPolygonTest(hull, (float(pt[0]), float(pt[1])), True)
        if signed_dist >= -margin_px:
            kept_points.append(pt)
            if detected_types is not None:
                kept_types.append(detected_types[idx])

    return kept_points, (kept_types if detected_types is not None else None)


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
    sheet_tilt_deg: float = 0.0        # inclinación de la grilla (grados); NaN si no medible
    tilt_warn: bool = False            # True cuando |sheet_tilt_deg| supera tilt_warn_deg


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
    if img_full is None or img_full.ndim < 2 or img_full.shape[0] == 0 or img_full.shape[1] == 0:
        raise ValueError(f"Imagen vacía o inválida: {image_path}")

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
    use_adaptive        = bool(tolerances.get("use_adaptive", False))
    adaptive_block_size = int(tolerances.get("adaptive_block_size", 61))
    adaptive_c          = float(tolerances.get("adaptive_c", -5.0))
    blur_ksize          = int(tolerances.get("blur_ksize", 5))
    open_ksize          = int(tolerances.get("open_ksize", 3))
    close_ksize         = int(tolerances.get("close_ksize", 5))
    edge_margin_px        = float(tolerances.get("edge_margin_px", 0.0))
    grid_max_missing      = int(tolerances.get("grid_max_missing", 0))
    frame_missing_nok_raw = tolerances.get("frame_missing_nok_threshold", None)
    frame_missing_nok_threshold = (
        None if frame_missing_nok_raw is None else int(frame_missing_nok_raw)
    )
    min_detection_ratio   = float(tolerances.get("min_detection_ratio", 0.30))
    quality_ratio_min     = float(tolerances.get("quality_ratio_min", 0.0))
    max_extra             = int(tolerances.get("max_extra", -1))
    bbox_filter_margin_px = float(tolerances.get("bbox_filter_margin_px", 0.0))
    grid_affine_refinement = bool(tolerances.get("grid_affine_refinement", False))
    # Umbral (grados) de inclinacion de la grilla para avisar "CHAPA INCLINADA".
    # 0.0 = solo medir, sin badge. La medicion siempre se calcula e informa.
    tilt_warn_deg          = float(tolerances.get("tilt_warn_deg", 0.0))
    # De-rotacion: corrige el matching cuando la chapa esta inclinada (de-rota los
    # agujeros segun el tilt medido antes de ajustar la grilla). Se aplica solo si
    # |tilt| supera grid_derotate_min_deg.
    grid_derotate          = bool(tolerances.get("grid_derotate", False))
    grid_derotate_min_deg  = float(tolerances.get("grid_derotate_min_deg", 0.4))
    # Verticalidad: si la chapa esta desviada (|tilt|>tilt_warn_deg), un solo frame
    # puede DETENER LA MAQUINA (parada inmediata). Los faltantes, en cambio, solo paran
    # por persistencia. Default False; se activa por modelo.
    machine_stop_on_tilt   = bool(tolerances.get("machine_stop_on_tilt", False))
    # Desalineacion del patron: si la fraccion de esperados sin matchear (tras el mejor
    # ajuste) supera este ratio, es un corrimiento/cizalla del patron → DETENER MAQUINA.
    pattern_desalign_enabled       = bool(tolerances.get("pattern_desalign_enabled", False))
    pattern_desalign_missing_ratio = float(tolerances.get("pattern_desalign_missing_ratio", 0.5))
    pattern_desalign_min_angle_deg = float(tolerances.get("pattern_desalign_min_angle_deg", 0.0))
    pattern_desalign_zigzag_std_px = float(tolerances.get("pattern_desalign_zigzag_std_px", 0.0))
    pattern_desalign_center_std_px = float(tolerances.get("pattern_desalign_center_std_px", 0.0))
    pattern_desalign_center_abs_px = float(tolerances.get("pattern_desalign_center_abs_px", 0.0))
    pattern_desalign_bottom_shift_px = float(tolerances.get("pattern_desalign_bottom_shift_px", 0.0))
    compare_top_ignore_px = float(tolerances.get("compare_top_ignore_px", 0.0))
    compare_bottom_ignore_px = float(tolerances.get("compare_bottom_ignore_px", 0.0))
    pattern_hull_margin_px = float(tolerances.get("pattern_hull_margin_px", 0.0))
    grid_extend_rows_after = int(tolerances.get("grid_extend_rows_after", 0))
    blur_score_min = float(tolerances.get("blur_score_min", 0.0))
    low_quality_max_streak = int(tolerances.get("low_quality_max_streak", 10))  # noqa: F841
    extra_min_dist_factor = float(tolerances.get("extra_min_dist_factor", 0.0))
    use_hungarian_matching = bool(tolerances.get("use_hungarian_matching", False))
    # Per-type hole classification (models with two distinct hole sizes, e.g. modelo_A)
    hole_type_split_area = float(tolerances.get("hole_type_split_area", 0.0))
    min_area_small = float(tolerances.get("min_area_small", 0.0))
    max_area_small = float(tolerances.get("max_area_small", 0.0))
    min_area_large = float(tolerances.get("min_area_large", 0.0))
    max_area_large = float(tolerances.get("max_area_large", 0.0))
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
    chapa_no_line_min_used_lines = int(tolerances.get("chapa_no_line_min_used_lines", 0))
    chapa_no_line_abs_max_px = float(tolerances.get("chapa_no_line_abs_max_px", 0.0))
    # PATRON edge zigzag → DETENER MAQUINA (mechanical misalignment, does not skip decisions)
    pattern_align_enabled     = bool(tolerances.get("pattern_align_enabled", False))
    pattern_align_std_max_px  = float(tolerances.get("pattern_align_std_max_px", 6.0))
    pattern_align_abs_max_px  = float(tolerances.get("pattern_align_abs_max_px", 15.0))
    pattern_global_offset_max_px = float(tolerances.get("pattern_global_offset_max_px", 0.0))
    pattern_slope_delta_max_deg = float(tolerances.get("pattern_slope_delta_max_deg", 0.0))
    # PATRON CENTER zigzag → same consequence as edge zigzag (finer internal misalignment)
    pattern_center_align_enabled    = bool(tolerances.get("pattern_center_align_enabled", False))
    pattern_center_zigzag_std_max   = float(tolerances.get("pattern_center_zigzag_std_max_px", 8.0))
    pattern_center_zigzag_abs_max   = float(tolerances.get("pattern_center_zigzag_abs_max_px", 18.0))

    # Camara Sony gran angular: en microperforado los checks basados en bordes laterales
    # y offset global generan falsos "UNSTABLE"/desalineado, aunque la grilla central
    # este bien. La inspeccion por matching de agujeros sigue siendo confiable.
    if model == "modelo_B":
        verticality_quality_enabled = False
        pattern_global_offset_max_px = 0.0

    edge_align_enabled = bool(tolerances.get("edge_align_enabled", True))
    if edge_align_enabled:
        img_aligned, align_res = align_image_by_right_edge(img_full, ema_state=ema_state)
    else:
        img_aligned = img_full
        align_res = EdgeAlignResult(angle_deg=0.0, used_lines=0)

    if roi is not None:
        try:
            img = apply_roi(img_aligned, roi)
        except ValueError as exc:
            raise ValueError(
                f"[{model}] ROI inválida para frame {img_aligned.shape[1]}x{img_aligned.shape[0]}: {exc}. "
                f"Recalibrá ROI con: python -m src.main define-roi --model {model}"
            ) from exc
    else:
        img = img_aligned

    # Warn if pattern was built at a different resolution than the current frame.
    # Critical for non-grid patterns (absolute coords); grid patterns are more tolerant
    # (dx/dy/phase scale-invariant) but still wrong if scale changed significantly.
    if pattern.image_size and pattern.image_size != (0, 0):
        pat_w, pat_h = pattern.image_size
        frame_w, frame_h = img.shape[1], img.shape[0]
        if pat_w != frame_w or pat_h != frame_h:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[%s] Patrón calibrado a %dx%d pero frame actual (post-ROI) es %dx%d. "
                "Resultados incorrectos — recalibrá con: "
                "build-pattern --model %s --scanner <scanner_id> --img <ref.jpg>",
                model, pat_w, pat_h, frame_w, frame_h, model,
            )

    preprocess_kw = dict(
        threshold=threshold, use_channel=use_channel, polarity=polarity,
        use_clahe=use_clahe, clahe_clip=clahe_clip, clahe_tile=clahe_tile,
        use_otsu=use_otsu,
        use_adaptive=use_adaptive, adaptive_block_size=adaptive_block_size, adaptive_c=adaptive_c,
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

    # Per-type filtering: when hole_type_split_area > 0, classify detected holes into
    # small/large by area and apply per-type min/max area bounds. Prevents large blobs
    # (noise/reflections) from being matched as large expected holes, and allows
    # tuning min_area independently for small vs large holes.
    _det_types_full: list[str] | None = None
    if hole_type_split_area > 0.0:
        _filtered_holes: list = []
        _types: list[str] = []
        for _h in holes:
            _ht = "small" if _h.area < hole_type_split_area else "large"
            _lo = (min_area_small if _ht == "small" else min_area_large) or min_area
            _hi = (max_area_small if _ht == "small" else max_area_large)
            if _h.area < _lo or (_hi > 0.0 and _h.area > _hi):
                continue
            _filtered_holes.append(_h)
            _types.append(_ht)
        holes = _filtered_holes
        detected_points = [(h.x, h.y) for h in holes]
        _det_types_full = _types

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
    sheet_tilt_deg = float("nan")

    if pattern.has_grid and detected_points:
        detected_arr = np.array(detected_points, dtype=np.float32)
        tol_affine = tol_xy_px * 1.5 if grid_affine_refinement else 0.0
        stagger_x_odd = float(pattern.stagger_x_odd) if pattern.stagger_x_odd is not None else 0.0

        # ── De-rotación por inclinación de la chapa ────────────────────────────
        # grid_compare_points asume grilla alineada a los ejes (barre fase X/Y). Con
        # la chapa inclinada una fila ya no está a `y` constante y la fase no engancha
        # → muchos falsos faltantes. Medimos el tilt de la grilla desde los agujeros,
        # de-rotamos los detectados, ajustamos la grilla en ese espacio y rotamos las
        # posiciones esperadas DE VUELTA al espacio original (donde se compara y dibuja).
        sheet_tilt_deg = estimate_lattice_tilt_deg(
            detected_arr,
            float(pattern.dx),
            dy=float(pattern.dy) if pattern.dy is not None else None,
        )
        _derotate = (
            grid_derotate
            and not math.isnan(sheet_tilt_deg)
            and abs(sheet_tilt_deg) > grid_derotate_min_deg
        )
        _cx, _cy = img_w / 2.0, img_h / 2.0
        det_for_grid = (
            rotate_points(detected_arr, -sheet_tilt_deg, _cx, _cy)
            if _derotate else detected_arr
        )
        grid_cells = list(pattern.cells)
        if grid_extend_rows_after > 0 and grid_cells:
            max_cj = max(cj for _ci, cj in grid_cells)
            existing = set(grid_cells)
            for row_offset in range(1, grid_extend_rows_after + 1):
                new_cj = max_cj + row_offset
                same_parity_rows = [
                    cj for _ci, cj in grid_cells
                    if cj < new_cj and cj % 2 == new_cj % 2
                ]
                if not same_parity_rows:
                    continue
                template_cj = max(same_parity_rows)
                for ci in sorted(ci for ci, cj in grid_cells if cj == template_cj):
                    cell = (ci, new_cj)
                    if cell not in existing:
                        existing.add(cell)
                        grid_cells.append(cell)

        compare_points, compare_cells = grid_compare_points(
            det_for_grid,
            grid_cells,
            pattern.dx,
            pattern.dy,
            pattern.phase_x,
            pattern.phase_y,
            None,
            img_w, img_h,
            edge_margin_px,
            tol_affine=tol_affine,
            stagger_x_odd=stagger_x_odd,
        )
        # Posiciones esperadas de vuelta al espacio original (imagen sin de-rotar).
        if _derotate and compare_points:
            compare_points = [
                (float(x), float(y)) for x, y in
                rotate_points(np.array(compare_points, dtype=np.float32),
                              sheet_tilt_deg, _cx, _cy)
            ]
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

    # Ignorar bordes superior/inferior solo en la etapa de comparacion.
    # Sirve para no penalizar filas extremas cuando la calibracion de los bordes
    # es inestable o el patron queda parcialmente cortado arriba/abajo.
    if compare_points and (compare_top_ignore_px > 0.0 or compare_bottom_ignore_px > 0.0):
        y_keep_min = compare_top_ignore_px
        y_keep_max = img_h - compare_bottom_ignore_px
        if compare_cells:
            _filtered = [
                (p, c) for p, c in zip(compare_points, compare_cells)
                if y_keep_min <= p[1] <= y_keep_max
            ]
            compare_points = [p for p, _ in _filtered]
            compare_cells = [c for _, c in _filtered]
        else:
            compare_points = [
                (x, y) for x, y in compare_points
                if y_keep_min <= y <= y_keep_max
            ]

    # Derive expected hole types from pattern radii (when type classification is active).
    # Split radius = sqrt(hole_type_split_area / π) — midpoint between small and large.
    expected_types: list[str] | None = None
    if hole_type_split_area > 0.0 and compare_cells and pattern.cells and pattern.radii:
        _split_r = (hole_type_split_area / 3.14159) ** 0.5
        _cell_to_r: dict = {
            (int(c[0]), int(c[1])): float(r)
            for c, r in zip(pattern.cells, pattern.radii)
        }
        _r_by_ci_parity: dict[tuple[int, int], list[float]] = {}
        _r_by_parity: dict[int, list[float]] = {}
        for c, r in zip(pattern.cells, pattern.radii):
            ci, cj = int(c[0]), int(c[1])
            _r_by_ci_parity.setdefault((ci, cj % 2), []).append(float(r))
            _r_by_parity.setdefault(cj % 2, []).append(float(r))

        def _expected_radius(c: tuple[int, int]) -> float:
            ci, cj = int(c[0]), int(c[1])
            exact = _cell_to_r.get((ci, cj))
            if exact is not None:
                return exact
            same_col = _r_by_ci_parity.get((ci, cj % 2))
            if same_col:
                return float(np.median(same_col))
            same_parity = _r_by_parity.get(cj % 2)
            if same_parity:
                return float(np.median(same_parity))
            return _split_r

        expected_types = [
            "small" if _expected_radius(c) < _split_r else "large"
            for c in compare_cells
        ]

    # Filtrar detecciones al bounding box del patrón esperado para eliminar agujeros
    # reales del material fuera de la ventana del patrón (reducen extra y costo de matching).
    # Los holes originales se mantienen intactos para el cálculo de centrado.
    detected_types: list[str] | None = None
    if compare_points and bbox_filter_margin_px >= 0:
        xs = [p[0] for p in compare_points]
        ys = [p[1] for p in compare_points]
        m = bbox_filter_margin_px
        bx1, bx2 = min(xs) - m, max(xs) + m
        by1, by2 = min(ys) - m, max(ys) + m
        if _det_types_full is not None:
            _bbox_pairs = [
                (pt, dt) for pt, dt in zip(detected_points, _det_types_full)
                if bx1 <= pt[0] <= bx2 and by1 <= pt[1] <= by2
            ]
            detected_in_bbox = [p for p, _ in _bbox_pairs]
            detected_types   = [t for _, t in _bbox_pairs]
        else:
            detected_in_bbox = [(x, y) for x, y in detected_points
                                if bx1 <= x <= bx2 and by1 <= y <= by2]
    else:
        detected_in_bbox = detected_points
        detected_types   = _det_types_full

    if detected_in_bbox and (compare_top_ignore_px > 0.0 or compare_bottom_ignore_px > 0.0):
        y_keep_min = compare_top_ignore_px
        y_keep_max = img_h - compare_bottom_ignore_px
        if detected_types is not None:
            _filtered = [
                (pt, dt) for pt, dt in zip(detected_in_bbox, detected_types)
                if y_keep_min <= pt[1] <= y_keep_max
            ]
            detected_in_bbox = [p for p, _ in _filtered]
            detected_types = [t for _, t in _filtered]
        else:
            detected_in_bbox = [
                (x, y) for x, y in detected_in_bbox
                if y_keep_min <= y <= y_keep_max
            ]

    _max_missing = grid_max_missing if (pattern.has_grid and detected_points) else 0
    report  = compare_missing_only(compare_points, detected_in_bbox,
                                   tol_xy_px=tol_xy_px, max_missing=_max_missing,
                                   max_extra=max_extra,
                                   extra_min_dist_factor=extra_min_dist_factor,
                                   expected_cells=compare_cells if compare_cells else None,
                                   use_hungarian=use_hungarian_matching,
                                   expected_types=expected_types,
                                   detected_types=detected_types)

    if report.extra_points and pattern_hull_margin_px > 0.0:
        filtered_extra_points, _ = _filter_points_by_pattern_hull(
            list(report.extra_points),
            compare_points,
            pattern_hull_margin_px,
        )
        if len(filtered_extra_points) != len(report.extra_points):
            report = CompareReport(
                expected=report.expected,
                detected=report.detected,
                missing=report.missing,
                status=report.status,
                missing_points=report.missing_points,
                matched_detected_idx=report.matched_detected_idx,
                extra=len(filtered_extra_points),
                extra_points=filtered_extra_points,
                missing_cells=report.missing_cells,
                missing_types=report.missing_types,
            )

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
    _ec_bands        = int(tolerances.get("edge_centering_bands", 16))
    _ec_min_holes    = int(tolerances.get("pattern_edge_min_holes_per_band", 1))
    _ec_smooth       = int(tolerances.get("pattern_edge_smooth_window", 1))
    _ec_boundary_tol = float(tolerances.get("pattern_edge_boundary_tol_px", 0.0))
    centering = compute_centering(
        img_aligned, holes, roi=roi, tol_px=center_offset_tol_px,
        n_bands=_ec_bands, min_holes_per_band=_ec_min_holes, smooth_window=_ec_smooth,
        boundary_tol_px=_ec_boundary_tol,
    )
    centering_nok = (
        centering is not None
        and not centering.within_tol
        and center_offset_tol_px > 0
    )
    missing_frame_nok = (
        frame_missing_nok_threshold is not None
        and report.missing > frame_missing_nok_threshold
    )
    final_status = "NOK" if (
        report.status == "NOK" or centering_nok or missing_frame_nok
    ) else report.status

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
    pattern_offset_warn = False
    pattern_slope_warn = False

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
            elif (
                chapa_no_line_min_used_lines > 0
                and chapa_no_line_abs_max_px > 0.0
                and int(align_res.used_lines) < chapa_no_line_min_used_lines
                and chapa_zigzag_max_px > chapa_no_line_abs_max_px
            ):
                frame_geometry_quality = "UNSTABLE"
                frame_quality = "LOW_QUALITY"  # weak external edge + sheet zigzag

        if pattern_align_enabled and frame_geometry_quality != "UNSTABLE":
            if (pattern_zigzag_std_px > pattern_align_std_max_px
                    or pattern_zigzag_max_px > pattern_align_abs_max_px):
                pattern_alignment_warn = True
                final_status = "NOK"   # desalineamiento mecánico del patron → NOK
            if (
                pattern_global_offset_max_px > 0.0
                and abs(centering.offset_px) > pattern_global_offset_max_px
            ):
                pattern_offset_warn = True
                pattern_alignment_warn = True
                final_status = "NOK"
            if (
                pattern_slope_delta_max_deg > 0.0
                and getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0)
                > pattern_slope_delta_max_deg
            ):
                pattern_slope_warn = True
                pattern_alignment_warn = True
                final_status = "NOK"

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
    # Inclinacion (tilt) de la grilla. Un frame inclinado suelto = NOK (sin parar);
    # solo si la inclinacion PERSISTE varios frames consecutivos se dispara la parada
    # por verticalidad (igual logica que los faltantes persistentes).
    tilt_warn = bool(
        tilt_warn_deg > 0.0
        and not math.isnan(sheet_tilt_deg)
        and abs(sheet_tilt_deg) > tilt_warn_deg
    )

    machine_stop = False
    _ms_reason   = "AGUJERO PERSISTENTE FALTANTE"
    if _ms_detector is not None:
        # FALTANTES: solo paran por PERSISTENCIA (>=2 frames). Los frames inclinados se
        # pasan como baja calidad para que la inclinacion (que genera muchos faltantes
        # que NO son punzon roto) no contamine ni dispare la parada por faltantes.
        # Un solo frame con faltantes NUNCA para (el metal pudo correrse).
        _ms_fq = "LOW_QUALITY" if tilt_warn else frame_quality
        machine_stop, _ms_positions = _ms_detector.update(
            report.missing_points, near_miss_pairs, _ms_fq, img_h,
            missing_cells=report.missing_cells,
        )
        if machine_stop:
            cols = _ms_detector.triggered_columns
            if cols:
                col_str  = ", ".join(str(c) for c in cols)
                _ms_reason = f"AGUJERO FALTANTE PERSISTENTE EN COLUMNA {col_str}"

    # VERTICALIDAD: un solo frame con la chapa desviada SI puede parar la maquina
    # (parada inmediata, a diferencia de los faltantes). Gated por machine_stop_on_tilt.
    if tilt_warn and machine_stop_on_tilt:
        machine_stop = True
        _ms_reason   = f"PATRON DESALINEADO - VERTICALIDAD {sheet_tilt_deg:+.1f} grados"
    if tilt_warn:
        final_status = "NOK"   # inclinado siempre NOK (pare o no)

    # DESALINEACION del patron (corrimiento de verticalidad / cizalla): cuando el patron
    # NO se puede ajustar como grilla, queda una fraccion MASIVA de esperados sin matchear
    # que ni la de-rotacion ni la fase ni el affine corrigen → es una falla geometrica real
    # (desalineacion mecanica), NO faltantes individuales. Un solo frame alcanza para parar.
    #   - chapa inclinada: la de-rotacion la ajusta → missing bajo → NO entra aca.
    #   - metal corrido: la fase lo encuentra → missing bajo → NO entra aca.
    #   - pocos faltantes (punzon/gap): fraccion baja → NO entra aca.
    # Se excluye cuando hay tilt_warn (chapa inclinada NUNCA para).
    if pattern_desalign_enabled and not tilt_warn and report.expected > 0:
        _missing_ratio = report.missing / report.expected
        _delta_ang = (
            float(getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0))
            if centering is not None else 0.0
        )
        if (_missing_ratio > pattern_desalign_missing_ratio
                and _delta_ang >= pattern_desalign_min_angle_deg):
            machine_stop = True
            _ms_reason   = (
                f"PATRON DESALINEADO ({report.missing}/{report.expected} sin ajustar, "
                f"dAng={_delta_ang:.1f} deg)"
            )
            final_status = "NOK"
        elif (
            centering is not None
            and pattern_desalign_zigzag_std_px > 0.0
            and pattern_desalign_center_std_px > 0.0
            and pattern_desalign_center_abs_px > 0.0
            and pattern_zigzag_std_px >= pattern_desalign_zigzag_std_px
            and pattern_center_zigzag_std_px >= pattern_desalign_center_std_px
            and pattern_center_zigzag_max_px >= pattern_desalign_center_abs_px
        ):
            machine_stop = True
            _ms_reason   = (
                f"PATRON DESALINEADO (zigzag={pattern_zigzag_std_px:.1f}px, "
                f"centro={pattern_center_zigzag_std_px:.1f}px)"
            )
            final_status = "NOK"
        elif pattern_desalign_bottom_shift_px > 0.0 and len(holes) >= 20:
            _hole_xy = np.array([(h.x, h.y) for h in holes], dtype=np.float32)
            _mid = _hole_xy[
                (_hole_xy[:, 1] >= img_h * 0.33)
                & (_hole_xy[:, 1] < img_h * 0.66)
            ]
            _bot = _hole_xy[_hole_xy[:, 1] >= img_h * 0.66]
            if len(_mid) >= 10 and len(_bot) >= 10:
                _bottom_shift = abs(float(np.median(_bot[:, 0]) - np.median(_mid[:, 0])))
                if _bottom_shift >= pattern_desalign_bottom_shift_px:
                    machine_stop = True
                    _ms_reason = f"PATRON DESALINEADO (corrimiento inferior={_bottom_shift:.1f}px)"
                    final_status = "NOK"

    if machine_stop:
        final_status = "NOK"

    # Build NOK cause list for the overlay panel (shown whenever final_status == "NOK")
    nok_reasons: list[str] = []
    if report.missing > 0:
        nok_reasons.append(f"AGUJEROS FALTANTES: {report.missing}")
    if report.extra_points:
        nok_reasons.append(f"AGUJEROS EXTRA: {len(report.extra_points)}")
    if centering_nok:
        nok_reasons.append(f"CENTRADO NOK ({centering.offset_px:+.1f}px)")
    if pattern_alignment_warn:
        if pattern_offset_warn:
            nok_reasons.append(f"PATRON DESCENTRADO ({centering.offset_px:+.1f}px)")
        if pattern_slope_warn:
            delta_ang = getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0)
            nok_reasons.append(f"PATRON INCLINADO ({delta_ang:.1f} deg)")
        if not pattern_offset_warn and not pattern_slope_warn:
            nok_reasons.append("PATRON DESALINEADO")
    if machine_stop:
        nok_reasons.append("PARADA DE MAQUINA")
    if frame_geometry_quality == "UNSTABLE":
        nok_reasons.append("IMAGEN INESTABLE")
    if not alignment_ok:
        nok_reasons.append("ALINEACION FALLBACK")

    if tilt_warn:
        nok_reasons.append(f"CHAPA INCLINADA {sheet_tilt_deg:+.1f} grados")

    badge_count = int(bool(machine_stop)) + int(bool(pattern_alignment_warn))

    overlay_holes = holes
    if compare_points:
        if bbox_filter_margin_px >= 0:
            xs = [p[0] for p in compare_points]
            ys = [p[1] for p in compare_points]
            m = bbox_filter_margin_px
            bx1, bx2 = min(xs) - m, max(xs) + m
            by1, by2 = min(ys) - m, max(ys) + m
            overlay_holes = [
                h for h in overlay_holes
                if bx1 <= h.x <= bx2 and by1 <= h.y <= by2
            ]
        if compare_top_ignore_px > 0.0 or compare_bottom_ignore_px > 0.0:
            y_keep_min = compare_top_ignore_px
            y_keep_max = img_h - compare_bottom_ignore_px
            overlay_holes = [
                h for h in overlay_holes
                if y_keep_min <= h.y <= y_keep_max
            ]
        if pattern_hull_margin_px > 0.0 and len(compare_points) >= 3:
            hull = cv2.convexHull(np.array(compare_points, dtype=np.float32).reshape(-1, 1, 2))
            overlay_holes = [
                h for h in overlay_holes
                if cv2.pointPolygonTest(hull, (float(h.x), float(h.y)), True) >= -pattern_hull_margin_px
            ]

    # Draw hole annotations on the ROI image (hole coords are in ROI space)
    overlay_roi = draw_compare_overlay(img, overlay_holes, report.missing_points, final_status,
                                       extra_points=report.extra_points,
                                       near_miss_pairs=near_miss_pairs,
                                       nok_reasons=nok_reasons,
                                       nok_panel_badge_count=badge_count,
                                       draw_status=False)  # estado se dibuja en el frame completo (izquierda)
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

    # Estado OK/NOK dibujado al borde IZQUIERDO del frame completo (zona oscura),
    # para no tapar los agujeros del patrón (que viven en la ROI, a la derecha).
    overlay = draw_status_indicator(overlay, final_status, nok_reasons, badge_count)
    overlay = draw_tilt_indicator(overlay, sheet_tilt_deg, warn=tilt_warn)
    overlay = draw_blur_indicator(overlay, blur_score, blur_score_min)

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
        sheet_tilt_deg=float(sheet_tilt_deg),
        tilt_warn=tilt_warn,
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

    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    image_paths = list(iter_image_files(input_dir))
    n = len(image_paths)

    # Pre-load shared read-only resources once (elimina N×3 lecturas de disco)
    _pre: dict = {
        "tolerances": tolerances,
        "pattern":    load_pattern(find_pattern_path(model, scanner_id)),
        "roi":        load_roi(model, scanner_id),
    }

    if ms_detector._enabled:
        # Secuencial obligatorio: MachineStopDetector tiene estado y debe ver
        # los frames en orden para acumular rachas correctamente.
        _pre["machine_stop_detector"] = ms_detector
        results = [
            inspect_image(model, path, save=save, scanner_id=scanner_id,
                          _preloaded=_pre)
            for path in image_paths
        ]
    else:
        # Paralelo seguro: sin detector con estado.
        # OpenCV y numpy liberan el GIL en operaciones pesadas.
        n_workers = min(os.cpu_count() or 2, 6)
        results_list: list = [None] * n

        def _worker(args):
            idx, path = args
            return idx, inspect_image(model, path, save=save,
                                      scanner_id=scanner_id, _preloaded=_pre)

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {
                executor.submit(_worker, (i, p)): i
                for i, p in enumerate(image_paths)
            }
            for future in as_completed(futures):
                idx, result = future.result()
                results_list[idx] = result

        results = results_list
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
