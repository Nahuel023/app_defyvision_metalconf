from pathlib import Path
from typing import Any

import yaml


DEFAULT_TOLERANCES: dict[str, Any] = {
    "threshold": 120,
    "use_channel": "r",
    "polarity": "bright",
    "min_area": 80,
    "max_area": None,       # None = sin límite superior; setear ~5× área esperada por modelo
    "circularity_min": 0.6,
    "tol_xy_px": 12.0,
    "aspect_ratio_max": 2.5,
    "align_match_tol_px": 80.0,
    "min_match_count": 8,
    "consecutive_nok_frames": 5,
    "frame_rate_hz": 5.0,
    "max_response_sec": 1.0,
    "edge_margin_px": 15.0,
    "use_clahe": False,
    "clahe_clip": 2.0,
    "clahe_tile": 8,
    "use_otsu": False,
    "use_adaptive": False,
    "adaptive_block_size": 61,
    "adaptive_c": -5.0,
    "tilt_warn_deg": 0.0,
    "blur_ksize": 5,
    "open_ksize": 3,
    "close_ksize": 5,
    "min_detection_ratio": 0.30,
    "max_extra": -1,
    "startup_selftest_enabled": False,
    "selftest_timeout_s": 10.0,
    "max_inspection_hz": 0,
    "grid_min_spacing": 30.0,
    # Frame-level visual decision: when missing > this value, result.status is NOK.
    # None means use the production compare threshold (grid_max_missing).
    "frame_missing_nok_threshold": None,
    "center_offset_tol_px": 0.0,   # 0 = medir y mostrar, nunca NOK; >0 = tolerancia activa
    "edge_centering_bands": 16,              # bandas horizontales para muestreo de bordes
    "pattern_edge_min_holes_per_band": 1,    # min agujeros por banda para incluirla en patrón
    "pattern_edge_smooth_window": 1,         # ventana de mediana para suavizar series de zigzag
    "pattern_edge_boundary_tol_px": 0.0,     # legacy/deprecated; IQR filter en edge_centering.py cubre este caso
    "grid_affine_refinement": False,  # True = corrección affine local post-fase-global
    "blur_score_min": 0.0,            # 0 = deshabilitado; >0 = varianza mín del Laplaciano
    "low_quality_max_streak": 10,     # frames LOW_QUALITY consecutivos antes de resetear racha
    "extra_min_dist_factor": 0.0,     # 0 = todos los extras se muestran; >0 = umbral en múltiplos de tol_xy_px
    "machine_stop_enabled": False,
    "machine_stop_missing_frames": 5,
    "machine_stop_min_missing": 1,
    "machine_stop_same_zone_px": 35.0,
    "machine_stop_ignore_near_miss": True,
    "machine_stop_track_by_grid": True,
    "machine_stop_same_column_tol_cells": 0,
    # Verticalidad: un solo frame con la chapa desviada puede parar la maquina
    # (inmediato). Los faltantes solo paran por persistencia (machine_stop_missing_frames).
    "machine_stop_on_tilt": False,
    # Desalineacion del patron (corrimiento de verticalidad/cizalla): si la fraccion de
    # esperados sin ajustar supera el ratio → DETENER MAQUINA (un frame alcanza).
    # ── Grabación de evidencia pre-evento ────────────────────────────────
    "events_enabled": False,           # activar en producción
    "events_max_disk_gb": 10.0,        # presupuesto total de data/events
    "pre_event_seconds": 60.0,         # 1 minuto antes de la parada
    "post_event_seconds": 30.0,        # 30 segundos después de la parada
    "pre_event_fps": 5.0,              # fotogramas/s a acumular en buffer
    "pre_event_jpeg_quality": 80,      # calidad JPEG del buffer (10-95)
    "pre_event_max_ram_mb": 256.0,     # 1 min × 5 fps × ~100 KB ≈ 30 MB; 256 con margen
    # ─────────────────────────────────────────────────────────────────────
    "pattern_desalign_enabled": False,
    "pattern_desalign_missing_ratio": 0.5,
    "pattern_desalign_min_angle_deg": 0.0,
    "pattern_desalign_zigzag_std_px": 0.0,
    "pattern_desalign_center_std_px": 0.0,
    "pattern_desalign_center_abs_px": 0.0,
    "pattern_desalign_bottom_shift_px": 0.0,
    "grid_extend_rows_after": 0,
    "use_hungarian_matching": False,
    "verticality_quality_enabled": False,
    "chapa_zigzag_std_max_px": 4.0,
    "chapa_zigzag_abs_max_px": 10.0,
    "chapa_no_line_min_used_lines": 0,
    "chapa_no_line_abs_max_px": 0.0,
    "pattern_align_enabled": False,
    "pattern_align_std_max_px": 5.0,
    "pattern_align_abs_max_px": 30.0,
    "pattern_global_offset_max_px": 0.0,
    "pattern_slope_delta_max_deg": 0.0,
    "pattern_center_align_enabled": False,
    "pattern_center_zigzag_std_max_px": 4.0,
    "pattern_center_zigzag_abs_max_px": 6.5,
    # Per-type hole classification (models with two distinct hole sizes, e.g. modelo_A)
    # 0 = disabled (single-type mode); >0 = area threshold (px²) between small/large holes
    "hole_type_split_area": 0.0,
    "min_area_small": 0.0,    # 0 = fall back to global min_area
    "max_area_small": 0.0,    # 0 = no upper limit for small holes
    "min_area_large": 0.0,    # 0 = fall back to global min_area
    "max_area_large": 0.0,    # 0 = no upper limit for large holes
}


def tolerances_path() -> Path:
    return Path("config/tolerancias.yaml")


def load_tolerances(model: str | None = None) -> dict[str, Any]:
    """Load tolerances, optionally merging per-model overrides.

    tolerancias.yaml structure:
        # global params (apply to every model)
        threshold: 175
        min_area: 80.0
        ...
        # per-model overrides (only specify what differs from global)
        models:
          modelo_A: {}        # Esterilla
          modelo_B:           # Microperforado
            min_area: 60.0
            tol_xy_px: 18.0
    """
    cfg_path = tolerances_path()
    if not cfg_path.exists():
        return dict(DEFAULT_TOLERANCES)

    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        return dict(DEFAULT_TOLERANCES)

    cfg = dict(DEFAULT_TOLERANCES)
    # Apply global params (skip the 'models' block)
    cfg.update({k: v for k, v in data.items() if k != "models" and v is not None})

    # Apply per-model overrides if a model is specified
    if model:
        model_overrides = (data.get("models") or {}).get(model) or {}
        cfg.update({k: v for k, v in model_overrides.items() if v is not None})

    return cfg


def save_model_overrides(model: str, updates: dict[str, Any]) -> None:
    """Actualiza solo los overrides de un modelo en tolerancias.yaml.

    Lee el archivo completo, parchea únicamente `models.<model>` con los
    valores de `updates` y lo reescribe. No toca parámetros globales ni
    otros modelos.
    """
    cfg_path = tolerances_path()
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    data.setdefault("models", {})[model] = dict(
        data["models"].get(model) or {},
        **{k: v for k, v in updates.items() if v is not None},
    )

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def save_tolerances(data: dict[str, Any]) -> None:
    cfg = dict(DEFAULT_TOLERANCES)
    cfg.update({k: v for k, v in data.items() if v is not None})

    cfg_path = tolerances_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
