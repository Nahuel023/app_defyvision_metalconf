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
    "blur_ksize": 5,
    "open_ksize": 3,
    "close_ksize": 5,
    "min_detection_ratio": 0.30,
    "max_extra": -1,
    "startup_selftest_enabled": False,
    "selftest_timeout_s": 10.0,
    "max_inspection_hz": 0,
    "grid_min_spacing": 30.0,
    "center_offset_tol_px": 0.0,   # 0 = medir y mostrar, nunca NOK; >0 = tolerancia activa
    "grid_affine_refinement": False,  # True = corrección affine local post-fase-global
    "blur_score_min": 0.0,            # 0 = deshabilitado; >0 = varianza mín del Laplaciano
    "low_quality_max_streak": 10,     # frames LOW_QUALITY consecutivos antes de resetear racha
    "extra_min_dist_factor": 0.0,     # 0 = todos los extras se muestran; >0 = umbral en múltiplos de tol_xy_px
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


def save_tolerances(data: dict[str, Any]) -> None:
    cfg = dict(DEFAULT_TOLERANCES)
    cfg.update({k: v for k, v in data.items() if v is not None})

    cfg_path = tolerances_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
