from pathlib import Path
from typing import Any

import yaml


DEFAULT_TOLERANCES: dict[str, Any] = {
    "threshold": 120,
    "use_channel": "r",
    "polarity": "bright",
    "min_area": 80,
    "circularity_min": 0.6,
    "tol_xy_px": 12.0,
    "aspect_ratio_max": 2.5,
    "align_match_tol_px": 80.0,
    "min_match_count": 8,
    "consecutive_nok_frames": 5,
    "frame_rate_hz": 5.0,
    "max_response_sec": 1.0,
}


def tolerances_path() -> Path:
    return Path("config/tolerancias.yaml")


def load_tolerances() -> dict[str, Any]:
    cfg_path = tolerances_path()
    if not cfg_path.exists():
        return dict(DEFAULT_TOLERANCES)

    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        return dict(DEFAULT_TOLERANCES)

    cfg = dict(DEFAULT_TOLERANCES)
    cfg.update({k: v for k, v in data.items() if v is not None})
    return cfg


def save_tolerances(data: dict[str, Any]) -> None:
    cfg = dict(DEFAULT_TOLERANCES)
    cfg.update({k: v for k, v in data.items() if v is not None})

    cfg_path = tolerances_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
