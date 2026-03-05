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


def roi_path(model: str) -> Path:
    return Path("data") / "patterns" / model / "roi.json"


def load_roi(model: str) -> Optional[ROI]:
    p = roi_path(model)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return ROI(int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"]))
    except Exception:
        return None


def apply_roi(img: np.ndarray, roi: ROI) -> np.ndarray:
    return img[roi.y:roi.y + roi.h, roi.x:roi.x + roi.w]