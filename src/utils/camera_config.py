"""Load/save per-scanner camera settings from config/camera.yaml."""
from pathlib import Path
from typing import Any

import yaml

_PATH          = Path("config/camera.yaml")
_PROFILES_PATH = Path("config/camera_profiles.yaml")

DEFAULTS: dict[str, Any] = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
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
    "zoom": 100,    # % digital zoom; 100 = sin zoom
    "pan_x": 0,     # -100..100, desplazamiento horizontal del recorte
    "pan_y": 0,     # -100..100, desplazamiento vertical del recorte
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
    """Merge and persist settings for scanner_id; preserves other scanners
    and any existing keys for this scanner not present in `settings`."""
    existing: dict[str, Any] = {}
    if _PATH.exists():
        with _PATH.open("r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    existing.setdefault(scanner_id, {}).update(settings)
    with _PATH.open("w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)


def load_camera_profiles() -> dict[str, dict[str, Any]]:
    """Return all profiles (built-in + user) keyed by slug."""
    if not _PROFILES_PATH.exists():
        return {}
    with _PROFILES_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    result: dict[str, dict[str, Any]] = {}
    for section in ("profiles", "user_profiles"):
        block = data.get(section) or {}
        if isinstance(block, dict):
            result.update(block)
    return result


def save_user_profile(slug: str, profile: dict[str, Any]) -> None:
    """Persist a user-defined profile to camera_profiles.yaml."""
    data: dict[str, Any] = {}
    if _PROFILES_PATH.exists():
        with _PROFILES_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data.setdefault("user_profiles", {})[slug] = profile
    with _PROFILES_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)
