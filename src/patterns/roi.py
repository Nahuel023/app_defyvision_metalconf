import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

_log = logging.getLogger(__name__)


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
    if img is None or img.ndim < 2:
        raise ValueError("apply_roi: imagen invalida")

    h, w = img.shape[:2]
    x1 = max(0, int(roi.x))
    y1 = max(0, int(roi.y))
    x2 = min(w, int(roi.x + roi.w))
    y2 = min(h, int(roi.y + roi.h))
    if x1 >= x2 or y1 >= y2:
        raise ValueError(
            "apply_roi: ROI fuera de imagen o vacia "
            f"(roi=x={roi.x},y={roi.y},w={roi.w},h={roi.h}; image={w}x{h})"
        )
    if x1 != roi.x or y1 != roi.y or x2 != roi.x + roi.w or y2 != roi.y + roi.h:
        _log.warning(
            "ROI recortada: pedida (x=%d,y=%d,w=%d,h=%d) sobre imagen %dx%d "
            "→ resultado (%d,%d,%d,%d). La ROI fue calibrada para otra resolución.",
            roi.x, roi.y, roi.w, roi.h, w, h, x1, y1, x2 - x1, y2 - y1,
        )
    return img[y1:y2, x1:x2]
