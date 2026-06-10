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


def detect_roi_from_images(
    imgs: list,
    channel: str = "r",
    margin_px: int = 0,
    min_contrast: float = 30.0,
    search_half: float = 0.6,
) -> Optional["ROI"]:
    """Auto-detect the metal sheet ROI from one or more frames.

    Uses the column intensity profile of the chosen channel.  The backlight
    produces bright columns outside the metal sheet; the sheet itself is dark.
    The left edge is the sharpest bright→dark drop and the right edge is the
    sharpest dark→bright rise.  Results are averaged (median) across frames.

    Parameters
    ----------
    imgs:        list of BGR ndarrays (all same shape).
    channel:     'r', 'g', 'b', or 'gray'.  'r' works best with red backlights.
    margin_px:   pixels to add *inward* from each detected edge (positive shrinks
                 the ROI away from the backlight transition zone).
    min_contrast: minimum bright/dark contrast required to trust the detection.
    search_half: fraction of the image width to search for each edge (prevents
                 the left-edge search from picking up the right backlight and
                 vice-versa).

    Returns a ROI with y=0, h=frame_height, or None if detection fails.
    """
    import cv2 as _cv2

    if not imgs:
        return None

    H, W = imgs[0].shape[:2]
    left_xs: list[int] = []
    right_xs: list[int] = []

    for img in imgs:
        if channel == "r":
            ch = img[:, :, 2].astype(np.float32)
        elif channel == "g":
            ch = img[:, :, 1].astype(np.float32)
        elif channel == "b":
            ch = img[:, :, 0].astype(np.float32)
        else:
            ch = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY).astype(np.float32)

        # 20th-percentile per column: robust against bright holes in the metal
        col_prof = np.percentile(ch, 20, axis=0).astype(np.float32)

        k = max(5, W // 50)
        k = k + 1 if k % 2 == 0 else k
        col_s = _cv2.GaussianBlur(col_prof.reshape(1, -1), (k, 1), 0).ravel()

        contrast = float(col_s.max() - col_s.min())
        if contrast < min_contrast:
            continue

        grad = np.diff(col_s)
        min_grad_mag = contrast * 0.04   # at least 4 % of total swing

        # Left edge: steepest bright→dark drop in the left search_half
        left_search = int(W * search_half)
        idx_l = int(np.argmin(grad[:left_search]))
        if -grad[idx_l] >= min_grad_mag:
            left_xs.append(idx_l)

        # Right edge: steepest dark→bright rise in the right search_half
        right_start = W - int(W * search_half)
        idx_r = int(np.argmax(grad[right_start:])) + right_start + 1  # +1: diff offset
        if grad[idx_r - 1] >= min_grad_mag:
            right_xs.append(idx_r)

    if not left_xs or not right_xs:
        return None

    lx = int(np.median(left_xs))  + margin_px
    rx = int(np.median(right_xs)) - margin_px

    if rx <= lx:
        return None

    lx = max(0, lx)
    rx = min(W, rx)
    return ROI(x=lx, y=0, w=rx - lx, h=H)


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
