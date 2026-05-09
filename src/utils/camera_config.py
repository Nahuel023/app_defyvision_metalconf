"""Load/save per-scanner camera settings from config/camera.yaml."""
from pathlib import Path
from typing import Any

import yaml

_PATH = Path("config/camera.yaml")

DEFAULTS: dict[str, Any] = {
    "autofocus": True,
    "focus": 100,
    "auto_exposure": True,
    "exposure": -7,
    "auto_white_balance": True,
    "white_balance": 4500,
    "gain": 0,
    "brightness": 128,
    "contrast": 140,
    "saturation": 50,
    "sharpness": 160,
    "gamma": 110,
    "backlight_compensation": 0,
}


def load_camera_settings(scanner_id: str) -> dict[str, Any]:
    """Return merged settings: DEFAULTS ← camera.yaml[scanner_id]."""
    out = dict(DEFAULTS)
    if _PATH.exists():
        with _PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        out.update(data.get(scanner_id, {}))
    return out


def save_camera_settings(scanner_id: str, settings: dict[str, Any]) -> None:
    """Persist settings for scanner_id; preserves other scanners."""
    existing: dict[str, Any] = {}
    if _PATH.exists():
        with _PATH.open("r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    existing[scanner_id] = settings
    with _PATH.open("w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)
