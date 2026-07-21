import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np

from src.io.load_images import load_bgr_image
from src.io.save_results import save_image
from src.patterns.pattern_io import load_pattern, find_pattern_path, Pattern, infer_scanner_id
from src.patterns.roi import apply_roi, load_roi, roi_path, ROI, RuntimeROIInfo, resolve_runtime_roi, load_roi_drift_state, save_roi_drift_state
from src.pipeline.align_edge import EdgeAlignResult, align_image_by_right_edge
from src.pipeline.annotate import draw_compare_overlay, draw_centering_overlay, draw_machine_stop_badge, draw_status_indicator, draw_tilt_indicator, draw_blur_indicator, draw_roi_indicator, draw_roi_health_indicator
from src.pipeline.machine_stop import MachineStopDetector
from src.pipeline.compare import CompareReport, compare_missing_only
from src.pipeline.detect_holes import Hole, detect_holes_from_mask
from src.pipeline.edge_centering import CenteringResult, compute_centering
from src.pipeline.grid_fitting import grid_compare_points, estimate_lattice_tilt_deg, rotate_points
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


logger = logging.getLogger(__name__)


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
    image: "np.ndarray | None" = None        # frame crudo (antes del overlay) para guardar raw
    pattern_alignment_warn: bool = False     # True cuando el PATRON zigzaguea (desalineado)
    chapa_zigzag_std_px: float = 0.0
    chapa_zigzag_max_px: float = 0.0
    pattern_zigzag_std_px: float = 0.0
    pattern_zigzag_max_px: float = 0.0
    pattern_center_zigzag_std_px: float = 0.0
    pattern_center_zigzag_max_px: float = 0.0
    sheet_tilt_deg: float = 0.0        # inclinación de la grilla (grados); NaN si no medible
    tilt_warn: bool = False            # True cuando |sheet_tilt_deg| supera tilt_warn_deg
    active_roi: "ROI | None" = None
    roi_info: "RuntimeROIInfo | None" = None


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
    scanner_id = scanner_id or infer_scanner_id(model, img_path)
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

    # Estado de racha de desalineacion del patron (persiste entre frames igual que ema_state).
    # Formato: {"streak": int, "reason": str}
    desalign_state: dict = pre.get("desalign_state") or {"streak": 0, "reason": ""}

    tolerances = pre.get("tolerances") or load_tolerances(model, scanner_id=scanner_id)
    pattern: Pattern = pre.get("pattern") or load_pattern(find_pattern_path(model, scanner_id))
    roi: Optional[ROI] = pre.get("roi", _SENTINEL)
    if roi is _SENTINEL:
        roi = load_roi(model, scanner_id)
    saved_roi: Optional[ROI] = pre.get("saved_roi", roi)
    roi_runtime_state: dict = pre.get("roi_runtime_state") or {}
    pre["roi_runtime_state"] = roi_runtime_state
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
    # Margen separado para la generación de celdas esperadas en modo grilla.
    # Permite excluir celdas proyectadas cerca del borde del frame (artefactos de borde)
    # sin afectar la detección de agujeros. Defecto = edge_margin_px.
    grid_compare_margin_px = float(tolerances.get("grid_compare_margin_px", edge_margin_px))
    _gcm_x_raw = tolerances.get("grid_compare_margin_x_px")
    _gcm_y_raw = tolerances.get("grid_compare_margin_y_px")
    grid_compare_margin_x_px = float(_gcm_x_raw) if _gcm_x_raw is not None else None
    grid_compare_margin_y_px = float(_gcm_y_raw) if _gcm_y_raw is not None else None
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
    grid_allow_row_parity_flip = bool(
        tolerances.get("grid_allow_row_parity_flip", False)
    )
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
    machine_stop_require_frame_nok = bool(
        tolerances.get("machine_stop_require_frame_nok", False)
    )
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
    compare_left_ignore_px = float(tolerances.get("compare_left_ignore_px", 0.0))
    compare_right_ignore_px = float(tolerances.get("compare_right_ignore_px", 0.0))
    pattern_hull_margin_px = float(tolerances.get("pattern_hull_margin_px", 0.0))
    grid_extend_rows_before = int(tolerances.get("grid_extend_rows_before", 0))
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
    pattern_align_min_missing = int(tolerances.get("pattern_align_min_missing", 0))
    pattern_align_std_max_px  = float(tolerances.get("pattern_align_std_max_px", 6.0))
    pattern_align_abs_max_px  = float(tolerances.get("pattern_align_abs_max_px", 15.0))
    # Cuando True, pattern_alignment_warn acumula una racha de frames desalineados antes de parar.
    # Cuando False, solo marca NOK + badge sin parar la maquina nunca.
    pattern_align_machine_stop = bool(tolerances.get("pattern_align_machine_stop", True))
    # Frames consecutivos con desalineacion de patron necesarios para disparar machine_stop.
    # 1 = parada inmediata en el primer frame; 3 = requiere 3 frames seguidos (mas robusto al ruido).
    pattern_align_stop_frames  = int(tolerances.get("pattern_align_stop_frames", 3))
    pattern_global_offset_max_px = float(tolerances.get("pattern_global_offset_max_px", 0.0))
    pattern_slope_delta_max_deg = float(tolerances.get("pattern_slope_delta_max_deg", 0.0))
    # Desalineacion SEVERA de un solo frame (zigzag de borde muy por encima del umbral
    # de advertencia): para de inmediato sin esperar la racha de pattern_align_stop_frames,
    # porque un solo frame asi ya es indicio mecanico, no ruido normal de borde de chapa.
    # 0.0 = desactivado (default; requiere calibrar con frames reales antes de habilitar).
    pattern_align_severe_abs_max_px = float(tolerances.get("pattern_align_severe_abs_max_px", 0.0))
    pattern_align_severe_slope_deg  = float(tolerances.get("pattern_align_severe_slope_deg", 0.0))
    # PATRON CENTER zigzag → same consequence as edge zigzag (finer internal misalignment)
    pattern_center_align_enabled    = bool(tolerances.get("pattern_center_align_enabled", False))
    pattern_center_zigzag_std_max   = float(tolerances.get("pattern_center_zigzag_std_max_px", 8.0))
    pattern_center_zigzag_abs_max   = float(tolerances.get("pattern_center_zigzag_abs_max_px", 18.0))

    # Nota: verticality_quality_enabled y pattern_global_offset_max_px se controlan
    # desde tolerancias.yaml por modelo. Para modelo_B mantener ambos en false/0
    # a menos que se valide que el centering de bordes es confiable con la camara usada.
    roi_autocorrect_enabled = bool(tolerances.get("roi_autocorrect_enabled", False))
    roi_autocorrect_max_shift_px = float(tolerances.get("roi_autocorrect_max_shift_px", 0.0))
    roi_autocorrect_max_width_delta_px = float(
        tolerances.get("roi_autocorrect_max_width_delta_px", 0.0)
    )
    roi_detect_margin_px = int(tolerances.get("roi_detect_margin_px", 0))
    roi_detect_min_contrast = float(tolerances.get("roi_detect_min_contrast", 30.0))
    roi_recenter_enabled = bool(tolerances.get("roi_recenter_enabled", False))
    roi_recenter_warmup_frames = int(tolerances.get("roi_recenter_warmup_frames", 20))
    roi_recenter_trigger_delta_px = float(tolerances.get("roi_recenter_trigger_delta_px", 6.0))
    roi_recenter_edge_missing_min = int(tolerances.get("roi_recenter_edge_missing_min", 3))
    roi_recenter_edge_band_px = float(tolerances.get("roi_recenter_edge_band_px", 28.0))
    roi_recenter_streak_frames = int(tolerances.get("roi_recenter_streak_frames", 6))
    roi_recenter_step_px = float(tolerances.get("roi_recenter_step_px", 1.0))
    roi_recenter_max_total_shift_px = float(tolerances.get("roi_recenter_max_total_shift_px", 40.0))
    roi_recenter_urgent_delta_px = float(tolerances.get("roi_recenter_urgent_delta_px", 15.0))
    roi_recenter_cooldown_frames = int(tolerances.get("roi_recenter_cooldown_frames", 15))
    roi_recenter_cooldown_max_frames = int(tolerances.get("roi_recenter_cooldown_max_frames", 120))
    roi_recenter_cooldown_mult = float(tolerances.get("roi_recenter_cooldown_mult", 2.0))
    roi_recenter_mode = str(tolerances.get("roi_recenter_mode", "resize"))
    roi_recenter_require_edge_missing = bool(
        tolerances.get("roi_recenter_require_edge_missing", True)
    )
    roi_recenter_max_width_growth_px = float(tolerances.get("roi_recenter_max_width_growth_px", 60.0))
    roi_slow_ema_enabled        = bool(tolerances.get("roi_slow_ema_enabled", False))
    roi_slow_ema_alpha          = float(tolerances.get("roi_slow_ema_alpha", 0.002))
    roi_slow_ema_threshold_px   = float(tolerances.get("roi_slow_ema_threshold_px", 15.0))
    roi_slow_ema_confirm_frames = int(tolerances.get("roi_slow_ema_confirm_frames", 500))
    roi_slow_ema_cooldown_frames= int(tolerances.get("roi_slow_ema_cooldown_frames", 1500))
    roi_slow_ema_max_total_px   = int(tolerances.get("roi_slow_ema_max_total_px", 40))
    roi_slow_ema_save_every     = int(tolerances.get("roi_slow_ema_save_every", 300))
    roi_slow_ema_warmup_frames  = int(tolerances.get("roi_slow_ema_warmup_frames", 300))

    edge_align_enabled = bool(tolerances.get("edge_align_enabled", True))
    if edge_align_enabled:
        img_aligned, align_res = align_image_by_right_edge(img_full, ema_state=ema_state)
    else:
        img_aligned = img_full
        align_res = EdgeAlignResult(angle_deg=0.0, used_lines=0)

    roi_info: RuntimeROIInfo | None = None
    if roi is not None:
        roi, roi_info = resolve_runtime_roi(
            img_aligned,
            roi,
            auto_correct_enabled=roi_autocorrect_enabled,
            max_shift_px=roi_autocorrect_max_shift_px,
            max_width_delta_px=roi_autocorrect_max_width_delta_px,
            channel=use_channel,
            margin_px=roi_detect_margin_px,
            min_contrast=roi_detect_min_contrast,
        )
        try:
            img = apply_roi(img_aligned, roi)
        except ValueError as exc:
            raise ValueError(
                f"[{model}] ROI inválida para frame {img_aligned.shape[1]}x{img_aligned.shape[0]}: {exc}. "
                f"Recalibrá ROI con: python -m src.main define-roi --model {model}"
            ) from exc
    else:
        img = img_aligned

    # Verificar que el patrón fue construido con la misma resolución que el frame actual.
    # Un mismatch significa que roi.json y holes.json están desincronizados. Se lanza
    # excepción para poner el scanner en ERROR de forma visible, en lugar de correr
    # silenciosamente con coordenadas incorrectas.
    # Tolerancia de 1px: absorbe clips de borde cuando roi.x+roi.w == frame_width.
    if pattern.image_size and pattern.image_size != (0, 0):
        pat_w, pat_h = pattern.image_size
        frame_w, frame_h = img.shape[1], img.shape[0]
        if abs(pat_w - frame_w) > 1 or abs(pat_h - frame_h) > 1:
            raise ValueError(
                f"[{model}] ROI y patrón desincronizados: "
                f"patrón construido a {pat_w}x{pat_h} pero frame actual (post-ROI) es {frame_w}x{frame_h}. "
                f"Recalibrá con: "
                f"build-pattern --model {model} --scanner {scanner_id or '<scanner_id>'} --img <ref.jpg>"
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

        # ── De-rotación por inclinación de la chapa ───────────────────
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
        if grid_extend_rows_before > 0 and grid_cells:
            min_cj = min(cj for _ci, cj in grid_cells)
            existing = set(grid_cells)
            for row_offset in range(1, grid_extend_rows_before + 1):
                new_cj = min_cj - row_offset
                same_parity_rows = [
                    cj for _ci, cj in grid_cells
                    if cj > new_cj and cj % 2 == new_cj % 2
                ]
                if not same_parity_rows:
                    continue
                template_cj = min(same_parity_rows)
                for ci in sorted(ci for ci, cj in grid_cells if cj == template_cj):
                    cell = (ci, new_cj)
                    if cell not in existing:
                        existing.add(cell)
                        grid_cells.append(cell)
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
            grid_compare_margin_px,
            tol_affine=tol_affine,
            stagger_x_odd=stagger_x_odd,
            margin_x=grid_compare_margin_x_px,
            margin_y=grid_compare_margin_y_px,
            allow_row_parity_flip=grid_allow_row_parity_flip,
            parity_selection_tol_px=tol_xy_px,
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

    # Ignorar bordes superior/inferior/izquierdo/derecho en la etapa de comparacion.
    # Sirve para no penalizar filas/columnas extremas cuando la calibracion de los bordes
    # es inestable o el patron queda parcialmente cortado en algun borde.
    _y_keep_min = compare_top_ignore_px
    _y_keep_max = img_h - compare_bottom_ignore_px
    _x_keep_min = compare_left_ignore_px
    _x_keep_max = img_w - compare_right_ignore_px
    _has_ignore = (compare_top_ignore_px > 0.0 or compare_bottom_ignore_px > 0.0
                   or compare_left_ignore_px > 0.0 or compare_right_ignore_px > 0.0)
    if compare_points and _has_ignore:
        if compare_cells:
            _filtered = [
                (p, c) for p, c in zip(compare_points, compare_cells)
                if _y_keep_min <= p[1] <= _y_keep_max and _x_keep_min <= p[0] <= _x_keep_max
            ]
            compare_points = [p for p, _ in _filtered]
            compare_cells = [c for _, c in _filtered]
        else:
            compare_points = [
                (x, y) for x, y in compare_points
                if _y_keep_min <= y <= _y_keep_max and _x_keep_min <= x <= _x_keep_max
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
    # holes_in_bbox se usa para compute_centering para estabilizar los bordes PATRON:
    # usar solo los agujeros en la región de comparación evita que detecciones extra
    # fuera del area del patrón desplacen global_left/global_right.
    detected_types: list[str] | None = None
    holes_in_bbox: list = holes  # default: all holes (updated below when bbox is applied)
    detected_holes_in_bbox: list = holes
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
        holes_in_bbox = [hh for hh in holes if bx1 <= hh.x <= bx2 and by1 <= hh.y <= by2]
        detected_holes_in_bbox = holes_in_bbox
    else:
        detected_in_bbox = detected_points
        detected_types   = _det_types_full

    # Filtrar detecciones solo por top/bottom (no por left/right).
    # El filtro X se aplica solo a compare_points (no a detected) porque los agujeros
    # detectados cerca del borde lateral siguen siendo validos para matchear con expected
    # points que estan dentro de la zona de comparacion pero cerca del margen.
    _has_tb_ignore = compare_top_ignore_px > 0.0 or compare_bottom_ignore_px > 0.0
    if detected_in_bbox and _has_tb_ignore:
        # Keep a matching halo around the compare boundary.  An expected point
        # can legitimately fall just inside the margin while its measured
        # centroid falls a few pixels outside because of curvature/perspective.
        # Removing that detection first creates a guaranteed false missing.
        _det_y_keep_min = max(0.0, _y_keep_min - tol_xy_px)
        _det_y_keep_max = min(float(img_h), _y_keep_max + tol_xy_px)
        detected_holes_in_bbox = [
            hh for hh in detected_holes_in_bbox
            if _det_y_keep_min <= hh.y <= _det_y_keep_max
        ]
        if detected_types is not None:
            _filtered = [
                (pt, dt) for pt, dt in zip(detected_in_bbox, detected_types)
                if _det_y_keep_min <= pt[1] <= _det_y_keep_max
            ]
            detected_in_bbox = [p for p, _ in _filtered]
            detected_types = [t for _, t in _filtered]
        else:
            detected_in_bbox = [
                (x, y) for x, y in detected_in_bbox
                if _det_y_keep_min <= y <= _det_y_keep_max
            ]

    _grid_match_max_dx_px: float | None = None
    if pattern.has_grid and detected_points and pattern.dx is not None:
        # En grilla no permitimos que una deteccion de la columna vecina
        # "tape" un agujero faltante cuando tol_xy_px es mas grande que dx.
        _grid_match_max_dx_px = min(
            float(tol_xy_px),
            max(6.0, float(pattern.dx) * 0.40),
        )

    _max_missing = grid_max_missing if (pattern.has_grid and detected_points) else 0
    report  = compare_missing_only(compare_points, detected_in_bbox,
                                   tol_xy_px=tol_xy_px,
                                   max_dx_px=_grid_match_max_dx_px,
                                   max_missing=_max_missing,
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
    _ec_chapa_inner  = int(tolerances.get("chapa_edge_inner_px", 80))
    centering = compute_centering(
        img_aligned, holes_in_bbox, roi=roi, tol_px=center_offset_tol_px,
        n_bands=_ec_bands, min_holes_per_band=_ec_min_holes, smooth_window=_ec_smooth,
        boundary_tol_px=_ec_boundary_tol, chapa_inner_px=_ec_chapa_inner,
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
    _desalign_stop   = False   # Desalineacion de patron: parada inmediata
    _desalign_reason = ""

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

        if pattern_align_enabled and frame_geometry_quality != "UNSTABLE" and frame_quality != "LOW_QUALITY":
            _pattern_align_missing_ok = (
                pattern_align_min_missing <= 0
                or report.missing >= pattern_align_min_missing
            )
            if (_pattern_align_missing_ok
                    and (pattern_zigzag_std_px > pattern_align_std_max_px
                         or pattern_zigzag_max_px > pattern_align_abs_max_px)):
                pattern_alignment_warn = True
                final_status = "NOK"   # desalineamiento mecánico del patron → NOK
                if pattern_align_machine_stop:
                    _desalign_stop = True
                    _desalign_reason = (
                        f"PATRON DESALINEADO - ZIGZAG BORDE "
                        f"(std={pattern_zigzag_std_px:.1f}px, max={pattern_zigzag_max_px:.1f}px)"
                    )
            if (
                _pattern_align_missing_ok
                and pattern_global_offset_max_px > 0.0
                and abs(centering.offset_px) > pattern_global_offset_max_px
            ):
                pattern_offset_warn = True
                pattern_alignment_warn = True
                final_status = "NOK"
                if pattern_align_machine_stop:
                    _desalign_stop = True
                    _desalign_reason = (
                        f"PATRON DESALINEADO - DESCENTRADO "
                        f"({centering.offset_px:+.1f}px)"
                    )
            if (
                _pattern_align_missing_ok
                and pattern_slope_delta_max_deg > 0.0
                and getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0)
                > pattern_slope_delta_max_deg
            ):
                pattern_slope_warn = True
                pattern_alignment_warn = True
                final_status = "NOK"
                if pattern_align_machine_stop:
                    _desalign_stop = True
                    _desalign_reason = (
                        f"PATRON DESALINEADO - INCLINACION "
                        f"({getattr(centering, 'pattern_sheet_slope_delta_max_deg', 0.0):.1f} deg)"
                    )

        if pattern_center_align_enabled and frame_geometry_quality != "UNSTABLE":
            _pattern_center_missing_ok = (
                pattern_align_min_missing <= 0
                or report.missing >= pattern_align_min_missing
            )
            if (_pattern_center_missing_ok
                    and (pattern_center_zigzag_std_px > pattern_center_zigzag_std_max
                         or pattern_center_zigzag_max_px > pattern_center_zigzag_abs_max)):
                pattern_alignment_warn = True
                final_status = "NOK"   # zigzag interno del patrón → NOK
                if pattern_align_machine_stop:
                    _desalign_stop = True
                    _desalign_reason = (
                        f"PATRON DESALINEADO - ZIGZAG CENTRO "
                        f"(std={pattern_center_zigzag_std_px:.1f}px, max={pattern_center_zigzag_max_px:.1f}px)"
                    )

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
        _ms_missing_points = report.missing_points
        _ms_missing_cells = report.missing_cells
        _ms_near_miss_pairs = near_miss_pairs
        if (
            machine_stop_require_frame_nok
            and frame_missing_nok_threshold is not None
            and report.missing <= frame_missing_nok_threshold
        ):
            # For microperforado, do not accumulate machine-stop evidence from
            # low-severity missing bursts that are still below the frame NOK gate.
            _ms_missing_points = []
            _ms_missing_cells = []
            _ms_near_miss_pairs = []
        # FALTANTES: solo paran por PERSISTENCIA (>=2 frames). Los frames inclinados se
        # pasan como baja calidad para que la inclinacion (que genera muchos faltantes
        # que NO son punzon roto) no contamine ni dispare la parada por faltantes.
        # Un solo frame con faltantes NUNCA para (el metal pudo correrse).
        _ms_fq = "LOW_QUALITY" if tilt_warn else frame_quality
        machine_stop, _ms_positions = _ms_detector.update(
            _ms_missing_points, _ms_near_miss_pairs, _ms_fq, img_h,
            missing_cells=_ms_missing_cells,
        )
        if machine_stop:
            _ms_reason = "AGUJERO FALTANTE PERSISTENTE"

    # DESALINEAMIENTO DE PATRON (zigzag de borde/centro): parada por RACHA de N frames.
    # _desalign_stop = True en este frame → incrementar racha.
    # _desalign_stop = False → resetear racha (ya no hay desalineacion).
    # Solo se para cuando racha >= pattern_align_stop_frames.
    # Se aplica DESPUES del ms_detector para no ser reseteado por machine_stop=False.
    # IMPORTANTE: guardar desalign_state de vuelta en pre[] para que persista al proximo frame
    # (mismo patron que ema_state: el dict mutado queda en _preloaded entre llamadas).
    if pattern_align_machine_stop:
        if _desalign_stop:
            desalign_state["streak"] += 1
            desalign_state["reason"]  = _desalign_reason
        else:
            desalign_state["streak"] = 0
            desalign_state["reason"] = ""
        if desalign_state["streak"] >= pattern_align_stop_frames:
            machine_stop = True
            _ms_reason   = (
                f"{desalign_state['reason']} "
                f"[{desalign_state['streak']}/{pattern_align_stop_frames} frames]"
            )
    else:
        desalign_state["streak"] = 0
        desalign_state["reason"] = ""
    # Persistir estado al siguiente frame (si _preloaded es un dict mutable compartido)
    pre["desalign_state"] = desalign_state

    # DESALINEACION SEVERA (1 solo frame, sin esperar racha): cuando el zigzag de borde
    # del patron supera por mucho el umbral de advertencia, ya no es ruido de borde de
    # chapa sino un corrimiento mecanico real — parar de inmediato evita que un
    # desalineamiento fuerte pero intermitente (no sostenido 2-3 frames seguidos)
    # se pierda porque la racha nunca llega a pattern_align_stop_frames.
    if (
        not machine_stop
        and pattern_align_enabled
        and frame_geometry_quality != "UNSTABLE"
        and frame_quality != "LOW_QUALITY"
        and centering is not None
        and (pattern_align_min_missing <= 0 or report.missing >= pattern_align_min_missing)
    ):
        if (
            pattern_align_severe_abs_max_px > 0.0
            and pattern_zigzag_max_px > pattern_align_severe_abs_max_px
        ):
            machine_stop = True
            _ms_reason   = (
                f"PATRON DESALINEADO - ZIGZAG SEVERO "
                f"(max={pattern_zigzag_max_px:.1f}px) [1 frame]"
            )
        elif (
            pattern_align_severe_slope_deg > 0.0
            and getattr(centering, "pattern_sheet_slope_delta_max_deg", 0.0) > pattern_align_severe_slope_deg
        ):
            machine_stop = True
            _ms_reason   = (
                f"PATRON DESALINEADO - INCLINACION SEVERA "
                f"({getattr(centering, 'pattern_sheet_slope_delta_max_deg', 0.0):.1f} deg) [1 frame]"
            )

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

    # Desplazamiento lateral del patron: si la nube de agujeros detectados se desplaza
    # mas de grid_lateral_shift_max_px respecto al patron de referencia, es una
    # Desviacion lateral del material: marca NOK + badge visual pero NO para la maquina
    # en un solo frame. El temporal logic (consecutive_nok_frames) decide la parada.
    # Gated por tilt_warn: con la chapa inclinada el centroide X puede desviarse
    # sin ser un corrimiento real del material.
    _lateral_shift_warn = False
    _lateral_shift_reason = ""
    grid_lateral_shift_max_px = float(tolerances.get("grid_lateral_shift_max_px", 0.0))
    if (grid_lateral_shift_max_px > 0.0
            and pattern.has_grid
            and detected_in_bbox
            and len(pattern.points) >= 10
            and not tilt_warn):
        # Usar detected_in_bbox (filtrado al bbox del patrón) para evitar que
        # detecciones extra fuera del area del patrón desplacen el centroide X.
        _det_mean_x = float(np.mean([x for x, _ in detected_in_bbox]))
        _pat_mean_x = float(np.mean([x for x, _ in pattern.points]))
        _lateral_shift = _det_mean_x - _pat_mean_x
        if abs(_lateral_shift) > grid_lateral_shift_max_px:
            _lateral_shift_warn = True
            _lateral_shift_reason = f"DESVIACION LATERAL ({_lateral_shift:+.1f}px)"
            final_status = "NOK"

    if machine_stop:
        final_status = "NOK"

    roi_info = _update_runtime_roi_drift(
        pre,
        roi,
        roi_info,
        report,
        model=model,
        scanner_id=scanner_id,
        enabled=roi_recenter_enabled,
        warmup_frames=roi_recenter_warmup_frames,
        trigger_delta_px=roi_recenter_trigger_delta_px,
        edge_missing_min=roi_recenter_edge_missing_min,
        edge_band_px=roi_recenter_edge_band_px,
        streak_frames=roi_recenter_streak_frames,
        step_px=roi_recenter_step_px,
        max_total_shift_px=roi_recenter_max_total_shift_px,
        urgent_delta_px=roi_recenter_urgent_delta_px,
        cooldown_frames=roi_recenter_cooldown_frames,
        cooldown_max_frames=roi_recenter_cooldown_max_frames,
        cooldown_mult=roi_recenter_cooldown_mult,
        recenter_mode=roi_recenter_mode,
        require_edge_missing=roi_recenter_require_edge_missing,
        max_width_growth_px=roi_recenter_max_width_growth_px,
    )

    _roi_slow_ema_step(
        pre,
        roi_info,
        model,
        scanner_id,
        enabled=roi_slow_ema_enabled,
        alpha=roi_slow_ema_alpha,
        threshold_px=roi_slow_ema_threshold_px,
        confirm_frames=roi_slow_ema_confirm_frames,
        cooldown_frames=roi_slow_ema_cooldown_frames,
        max_total_px=roi_slow_ema_max_total_px,
        save_every=roi_slow_ema_save_every,
        warmup_frames=roi_slow_ema_warmup_frames,
    )

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
    if _lateral_shift_warn:
        nok_reasons.append(_lateral_shift_reason)
    if machine_stop:
        nok_reasons.append("PARADA DE MAQUINA")
    if frame_geometry_quality == "UNSTABLE":
        nok_reasons.append("IMAGEN INESTABLE")
    if not alignment_ok:
        nok_reasons.append("ALINEACION FALLBACK")

    if tilt_warn:
        nok_reasons.append(f"CHAPA INCLINADA {sheet_tilt_deg:+.1f} grados")

    badge_count = int(bool(machine_stop)) + int(bool(pattern_alignment_warn)) + int(bool(_lateral_shift_warn))

    # Visual semantics for operators:
    # green = detected hole inside the active pattern comparison window
    # cyan  = raw detection outside that active window
    #
    # Using only report.matched_detected_idx made the overlay too strict: a hole
    # could be valid and relevant to the pattern region, but remain cyan simply
    # because the 1-to-1 matcher assigned a nearby neighbor to the expected point.
    # For operator feedback we want "participates in pattern analysis", not
    # "won the exact assignment slot".
    overlay_holes = list(detected_holes_in_bbox)

    # Draw hole annotations on the ROI image (hole coords are in ROI space)
    overlay_roi = draw_compare_overlay(img, overlay_holes, report.missing_points, final_status,
                                       raw_detected=holes,
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
        overlay = draw_roi_indicator(overlay, roi.x, roi.y, roi.w, roi.h)
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
        overlay = draw_machine_stop_badge(overlay, reason=f"{_ms_reason}  +  PATRON DESALINEADO", index=0)
    elif machine_stop:
        overlay = draw_machine_stop_badge(overlay, reason=_ms_reason, index=0)
    elif pattern_alignment_warn:
        # Solo advertencia (racha aun no llega a pattern_align_stop_frames): la maquina
        # NO se detuvo este frame. Titulo distinto a "DETENCION DE MAQUINA" para no
        # hacer creer al operador que ya paro cuando todavia no lo hizo.
        overlay = draw_machine_stop_badge(
            overlay, reason="PATRON DESALINEADO", index=0,
            title="ADVERTENCIA - PATRON DESALINEADO",
        )
    if _lateral_shift_warn:
        idx = int(bool(machine_stop or pattern_alignment_warn))
        overlay = draw_machine_stop_badge(overlay, reason=_lateral_shift_reason, index=idx, title="DESVIACION LATERAL")

    # Estado OK/NOK dibujado al borde IZQUIERDO del frame completo (zona oscura),
    # para no tapar los agujeros del patrón (que viven en la ROI, a la derecha).
    overlay = draw_status_indicator(overlay, final_status, nok_reasons, badge_count)
    overlay = draw_tilt_indicator(overlay, sheet_tilt_deg, warn=tilt_warn)
    overlay = draw_blur_indicator(overlay, blur_score, blur_score_min)
    overlay = draw_roi_health_indicator(overlay, roi_info)

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
        image=img_full,
        active_roi=roi,
        roi_info=roi_info,
    )


def _edge_missing_counts(
    missing_points: list[tuple[float, float]],
    roi_w: int,
    band_px: float,
) -> tuple[int, int]:
    if not missing_points or band_px <= 0.0 or roi_w <= 0:
        return 0, 0
    left = 0
    right = 0
    right_x0 = max(0.0, float(roi_w) - band_px)
    for x, _ in missing_points:
        if x <= band_px:
            left += 1
        if x >= right_x0:
            right += 1
    return left, right


def _roi_slow_ema_step(
    pre: dict,
    roi_info: "RuntimeROIInfo | None",
    model: str,
    scanner_id: "str | None",
    *,
    enabled: bool,
    alpha: float,
    threshold_px: float,
    confirm_frames: int,
    cooldown_frames: int,
    max_total_px: int,
    save_every: int,
    warmup_frames: int,
) -> None:
    if not enabled or roi_info is None or roi_info.detected_roi is None:
        return

    state = pre.get("roi_slow_ema")
    if state is None:
        state = load_roi_drift_state(model, scanner_id)
        pre["roi_slow_ema"] = state

    shift_x = float(roi_info.shift_x)
    n = int(state.get("n_frames", 0)) + 1
    state["n_frames"] = n

    # Fase 1: warmup - media simple para fijar baseline (captura offset estatico)
    if not state.get("baseline_ready", False):
        w_sum   = float(state.get("warmup_sum",   0.0)) + shift_x
        w_count = int(state.get("warmup_count",   0))   + 1
        state["warmup_sum"]   = w_sum
        state["warmup_count"] = w_count
        if w_count >= warmup_frames:
            baseline = w_sum / w_count
            state["ema_baseline"]   = round(baseline, 4)
            state["ema"]            = round(baseline, 4)
            state["baseline_ready"] = True
            logger.info(
                "[%s] ROI slow EMA: baseline=%.2fpx (%d frames)",
                scanner_id, baseline, w_count,
            )
        if n % save_every == 0:
            save_roi_drift_state(state, model, scanner_id)
        return

    # Fase 2: produccion - EMA lento, comparar delta vs baseline
    ema = float(state.get("ema", shift_x))
    ema = ema * (1.0 - alpha) + shift_x * alpha
    state["ema"] = round(ema, 4)

    baseline = float(state.get("ema_baseline", ema))
    delta    = ema - baseline

    cooldown = int(state.get("cooldown_remaining", 0))
    if cooldown > 0:
        state["cooldown_remaining"] = cooldown - 1
        if n % save_every == 0:
            save_roi_drift_state(state, model, scanner_id)
        return

    if delta >= threshold_px:
        direction = 1
    elif delta <= -threshold_px:
        direction = -1
    else:
        state["confirm_streak"] = 0
        if n % save_every == 0:
            save_roi_drift_state(state, model, scanner_id)
        return

    if state.get("confirm_dir", 0) == direction:
        streak = int(state.get("confirm_streak", 0)) + 1
    else:
        state["confirm_dir"] = direction
        streak = 1
    state["confirm_streak"] = streak

    if n % save_every == 0:
        save_roi_drift_state(state, model, scanner_id)

    if streak < confirm_frames:
        return

    # Drift confirmado: aplicar correccion de 1px
    current_roi = pre.get("roi")
    if current_roi is None:
        return

    applied_total = int(state.get("applied_total_px", 0))
    if abs(applied_total + direction) > max_total_px:
        logger.warning(
            "[%s] ROI slow EMA: limite maximo +/-%dpx alcanzado - recalibracion manual recomendada",
            scanner_id, max_total_px,
        )
        return

    new_x = max(0, min(roi_info.frame_w - current_roi.w, current_roi.x + direction))
    new_roi = ROI(x=new_x, y=current_roi.y, w=current_roi.w, h=current_roi.h)

    import json as _json
    p = roi_path(model, scanner_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            _json.dumps({"x": new_roi.x, "y": new_roi.y, "w": new_roi.w, "h": new_roi.h}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("[%s] ROI slow EMA: error escribiendo roi.json: %s", scanner_id, exc)
        return

    applied_total += direction
    state["applied_total_px"]   = applied_total
    state["confirm_streak"]     = 0
    state["cooldown_remaining"] = cooldown_frames
    state["ema_baseline"] = round(baseline + direction, 4)
    save_roi_drift_state(state, model, scanner_id)

    pre["roi"] = new_roi

    logger.info(
        "[%s] ROI slow EMA: %+dpx -> x=%d  (delta=%.1fpx  racha=%d  total=%+dpx)",
        scanner_id, direction, new_roi.x, delta, streak, applied_total,
    )



def _update_runtime_roi_drift(
    pre: dict,
    active_roi: ROI | None,
    roi_info: RuntimeROIInfo | None,
    report: CompareReport,
    *,
    model: str = "",
    scanner_id: str | None = None,
    enabled: bool,
    warmup_frames: int,
    trigger_delta_px: float,
    edge_missing_min: int,
    edge_band_px: float,
    streak_frames: int,
    step_px: float,
    max_total_shift_px: float,
    urgent_delta_px: float = 15.0,
    cooldown_frames: int = 15,
    cooldown_max_frames: int = 120,
    cooldown_mult: float = 2.0,
    recenter_mode: str = "resize",
    require_edge_missing: bool = True,
    max_width_growth_px: float = 60.0,
) -> RuntimeROIInfo | None:
    if not enabled or active_roi is None or roi_info is None or roi_info.detected_roi is None:
        return roi_info

    state = pre.get("roi_runtime_state") or {}
    pre["roi_runtime_state"] = state
    saved_roi = pre.get("saved_roi") or active_roi
    state.setdefault("baseline_shift_x", None)
    state.setdefault("baseline_samples", 0)
    state.setdefault("drift_streak", 0)
    state.setdefault("drift_dir", 0)
    state.setdefault("applied_total_shift_px", float(active_roi.x - saved_roi.x))
    state.setdefault("last_step_px", 0.0)
    state.setdefault("baseline_ready", False)
    state.setdefault("cooldown_remaining", 0)
    state.setdefault("current_cooldown", cooldown_frames)

    # Decrementar cooldown adaptativo
    cooldown_remaining = int(state.get("cooldown_remaining", 0))
    if cooldown_remaining > 0:
        state["cooldown_remaining"] = cooldown_remaining - 1
        return roi_info

    shift_x = float(roi_info.shift_x)
    baseline_shift_x = state.get("baseline_shift_x")
    baseline_samples = int(state.get("baseline_samples", 0))

    left_missing, right_missing = _edge_missing_counts(
        list(report.missing_points or []),
        active_roi.w,
        edge_band_px,
    )

    if baseline_shift_x is None:
        baseline_shift_x = shift_x
        baseline_samples = 1
    elif not state.get("baseline_ready", False):
        baseline_shift_x = ((baseline_shift_x * baseline_samples) + shift_x) / (baseline_samples + 1)
        baseline_samples += 1

    if baseline_samples >= max(1, warmup_frames):
        state["baseline_ready"] = True

    state["baseline_shift_x"] = baseline_shift_x
    state["baseline_samples"] = baseline_samples

    # Seguir cambios respecto de la relación calibrada durante el warmup. La ROI
    # de inspección puede estar intencionalmente desplazada respecto del centro
    # geométrico de la chapa, por lo que no debe perseguir shift_x == 0.
    drift_delta = shift_x - float(baseline_shift_x)
    is_urgent = abs(drift_delta) >= urgent_delta_px

    # Urgente: la magnitud sola es suficiente, no se necesitan agujeros faltantes en el borde
    if is_urgent:
        evidence_dir = 1 if drift_delta > 0 else -1
    else:
        evidence_dir = 0
        left_evidence = (not require_edge_missing) or left_missing >= edge_missing_min
        right_evidence = (not require_edge_missing) or right_missing >= edge_missing_min
        if drift_delta <= -trigger_delta_px and left_evidence:
            evidence_dir = -1
        elif drift_delta >= trigger_delta_px and right_evidence:
            evidence_dir = 1

    if evidence_dir == 0:
        state["drift_streak"] = 0
        state["drift_dir"] = 0
        state["last_step_px"] = 0.0
        return roi_info

    if int(state.get("drift_dir", 0)) == evidence_dir:
        state["drift_streak"] = int(state.get("drift_streak", 0)) + 1
    else:
        state["drift_dir"] = evidence_dir
        state["drift_streak"] = 1

    state["last_step_px"] = 0.0
    if not state.get("baseline_ready", False):
        return roi_info

    # Urgente actúa con 1 frame de racha; normal espera streak_frames
    required_streak = 1 if is_urgent else max(1, streak_frames)
    if int(state.get("drift_streak", 0)) < required_streak:
        return roi_info

    if recenter_mode == "resize":
        # Expande el borde en la dirección del drift sin mover el lado opuesto
        total_growth = float(active_roi.w - saved_roi.w)
        new_growth = total_growth + float(step_px)
        if max_width_growth_px > 0.0:
            new_growth = min(new_growth, max_width_growth_px)
        actual_step = new_growth - total_growth
        if actual_step < 0.5:
            state["drift_streak"] = 0
            return roi_info
        if evidence_dir > 0:
            # Contenido corrido a la derecha: expandir borde derecho
            new_w = int(round(active_roi.w + actual_step))
            new_x = active_roi.x
        else:
            # Contenido corrido a la izquierda: expandir borde izquierdo
            expand = int(round(actual_step))
            new_x = max(0, active_roi.x - expand)
            actual_expand = active_roi.x - new_x
            new_w = active_roi.w + actual_expand
        frame_w = roi_info.frame_w
        new_w = min(new_w, frame_w - new_x)
        new_roi = ROI(x=new_x, y=active_roi.y, w=new_w, h=active_roi.h)
        state["last_step_px"] = float(new_roi.w - active_roi.w) * evidence_dir
    else:
        # Modo move: desplaza toda la ventana
        current_total_shift = float(active_roi.x - saved_roi.x)
        target_total_shift = current_total_shift + (float(step_px) * evidence_dir)
        if max_total_shift_px > 0.0:
            target_total_shift = max(-max_total_shift_px, min(max_total_shift_px, target_total_shift))
        applied_step = target_total_shift - current_total_shift
        if abs(applied_step) < 0.5:
            state["drift_streak"] = 0
            return roi_info
        new_x = int(round(saved_roi.x + target_total_shift))
        max_x = max(0, roi_info.frame_w - active_roi.w)
        new_x = max(0, min(new_x, max_x))
        new_roi = ROI(x=new_x, y=active_roi.y, w=active_roi.w, h=active_roi.h)
        state["last_step_px"] = float(new_roi.x - active_roi.x)

    pre["roi"] = new_roi
    # Mantener saved_roi como ancla de la sesion hace que el limite total sea
    # realmente acumulado. Un guardado manual crea una sesión nueva y, por lo
    # tanto, establece intencionalmente una ancla nueva.
    if recenter_mode == "resize":
        state["applied_total_shift_px"] = float(new_roi.w - saved_roi.w)
    else:
        state["applied_total_shift_px"] = float(new_roi.x - saved_roi.x)
    state["drift_streak"] = 0

    # Cooldown adaptativo: crece con cada corrección hasta el máximo
    next_cooldown = int(min(
        int(state.get("current_cooldown", cooldown_frames)) * cooldown_mult,
        cooldown_max_frames,
    ))
    state["cooldown_remaining"] = next_cooldown
    state["current_cooldown"] = next_cooldown

    # Persistir en roi.json para que el próximo arranque parta de la posición corregida
    if model:
        import json as _json
        from src.patterns.roi import roi_path
        p = roi_path(model, scanner_id)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                _json.dumps({"x": new_roi.x, "y": new_roi.y, "w": new_roi.w, "h": new_roi.h}, indent=2),
                encoding="utf-8",
            )
            logger.info(
                "[%s] ROI recenter persistido: x=%d w=%d (paso=%+.0fpx  cooldown=%df  modo=%s)",
                scanner_id, new_roi.x, new_roi.w, state["last_step_px"], next_cooldown, recenter_mode,
            )
        except Exception as exc:
            logger.error("[%s] ROI recenter: error escribiendo roi.json: %s", scanner_id, exc)

    recenter_note = f"ROI recenter {state['last_step_px']:+.0f}px -> x={new_roi.x} w={new_roi.w} (cooldown={next_cooldown}f)"
    return RuntimeROIInfo(
        frame_w=roi_info.frame_w,
        frame_h=roi_info.frame_h,
        saved_roi=roi_info.saved_roi,
        effective_roi=roi_info.effective_roi,
        detected_roi=roi_info.detected_roi,
        shift_x=roi_info.shift_x,
        width_delta_px=roi_info.width_delta_px,
        auto_corrected=True,
        warning=f"{roi_info.warning} | {recenter_note}".strip(" |"),
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
    from src.vision.inspector import InspectionSession

    scanner_id = scanner_id or infer_scanner_id(model, input_dir)

    tolerances = load_tolerances(model, scanner_id=scanner_id)
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
    movement_threshold = float(tolerances.get("continuous_position_threshold", 0.0))

    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    image_paths = list(iter_image_files(input_dir))
    n = len(image_paths)

    # Pre-load shared read-only resources once (elimina N×3 lecturas de disco)
    session = InspectionSession(
        model,
        scanner_id=scanner_id,
        save=save,
        movement_threshold=movement_threshold,
        min_interval_sec=0.0,
    )

    if "machine_stop_detector" in session._preloaded:
        results = []
        for path in image_paths:
            result = session.inspect_path(path)
            if result is not None:
                results.append(result)
    else:
        # Paralelo seguro: sin detector con estado.
        # OpenCV y numpy liberan el GIL en operaciones pesadas.
        n_workers = min(os.cpu_count() or 2, 6)
        results_list: list = [None] * n

        def _worker(args):
            idx, path = args
            return idx, inspect_image(model, path, save=save, scanner_id=scanner_id)

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
    """Guarda overlay en data/output/ok|nok y máscara en data/output/debug.
    También guarda el frame crudo (_raw.jpg) junto al overlay para permitir toggle en el visor."""
    out_dir = Path("data/output/ok") if result.status == "OK" else Path("data/output/nok")
    dbg_dir = Path("data/output/debug")
    save_image(dbg_dir / f"{result.image_path.stem}_mask.png", result.mask)
    save_image(out_dir  / f"{result.image_path.stem}_overlay.png", result.overlay)
    if result.image is not None:
        raw_path = out_dir / f"{result.image_path.stem}_raw.jpg"
        out_dir.mkdir(parents=True, exist_ok=True)
        import cv2 as _cv2
        _cv2.imwrite(str(raw_path), result.image, [_cv2.IMWRITE_JPEG_QUALITY, 85])


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
