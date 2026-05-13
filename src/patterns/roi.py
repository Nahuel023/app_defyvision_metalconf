import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    w: int
    h: int


def roi_path(model: str, scanner_id: str | None = None) -> Path:
    """Return the canonical write path for a ROI (does not check existence)."""
    if scanner_id:
        return Path("data") / "patterns" / scanner_id / model / "roi.json"
    return Path("data") / "patterns" / model / "roi.json"


def load_roi(model: str, scanner_id: str | None = None) -> Optional[ROI]:
    """Load ROI with fallback: scanner-specific → model-only."""
    candidates = []
    if scanner_id:
        candidates.append(Path("data") / "patterns" / scanner_id / model / "roi.json")
    candidates.append(Path("data") / "patterns" / model / "roi.json")

    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            return ROI(int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"]))
        except Exception:
            continue
    return None


def apply_roi(img: np.ndarray, roi: ROI) -> np.ndarray:
    return img[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]