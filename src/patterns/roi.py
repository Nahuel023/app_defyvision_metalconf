import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from src.patterns.pattern_io import infer_scanner_id

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class RuntimeROIInfo:
    frame_w: int
    frame_h: int
    saved_roi: ROI
    effective_roi: ROI
    detected_roi: Optional[ROI] = None
    shift_x: float = 0.0
    width_delta_px: float = 0.0
    auto_corrected: bool = False
    warning: str = ""


def roi_path(model: str, scanner_id: str | None = None) -> Path:
    """Return the canonical write path for a ROI (does not check existence)."""
    if scanner_id:
        return Path("data") / "patterns" / scanner_id / model / "roi.json"
    return Path("data") / "patterns" / model / "roi.json"


def load_roi(model: str, scanner_id: str | None = None) -> Optional[ROI]:
    """Load ROI for the given scanner. Each scanner has its own ROI — no cross-scanner fallback.
    Falls back to the model-level roi.json only when scanner_id is not specified (e.g. CLI tools)."""
    scanner_id = scanner_id or infer_scanner_id(model)
    candidates = []
    if scanner_id:
        # Scanner-specific only — never fall back to another scanner's ROI
        candidates.append(Path("data") / "patterns" / scanner_id / model / "roi.json")
    else:
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


def _fit_roi_to_frame(roi: ROI, frame_w: int, frame_h: int) -> ROI:
    x = max(0, min(int(roi.x), max(0, frame_w - 1)))
    y = max(0, min(int(roi.y), max(0, frame_h - 1)))
    w = min(int(roi.w), max(1, frame_w - x))
    h = min(int(roi.h), max(1, frame_h - y))
    return ROI(x=x, y=y, w=w, h=h)


def resolve_runtime_roi(
    img: np.ndarray,
    saved_roi: ROI,
    *,
    auto_correct_enabled: bool = False,
    max_shift_px: float = 0.0,
    max_width_delta_px: float = 0.0,
    channel: str = "r",
    margin_px: int = 0,
    min_contrast: float = 30.0,
) -> tuple[ROI, RuntimeROIInfo]:
    """Validate the saved ROI against the current frame and optionally recenter it."""
    if img is None or img.ndim < 2:
        raise ValueError("resolve_runtime_roi: imagen invalida")

    frame_h, frame_w = img.shape[:2]
    fitted_roi = _fit_roi_to_frame(saved_roi, frame_w, frame_h)
    warning_parts: list[str] = []
    if fitted_roi != saved_roi:
        warning_parts.append("ROI recortada al frame")

    detected_roi = detect_roi_from_images(
        [img],
        channel=channel,
        margin_px=margin_px,
        min_contrast=min_contrast,
    )
    effective_roi = fitted_roi
    shift_x = 0.0
    width_delta_px = 0.0
    auto_corrected = False

    if detected_roi is not None:
        saved_center_x = fitted_roi.x + fitted_roi.w / 2.0
        detected_center_x = detected_roi.x + detected_roi.w / 2.0
        shift_x = detected_center_x - saved_center_x
        width_ratio = fitted_roi.w / max(float(detected_roi.w), 1.0)
        comparable_width = 0.7 <= width_ratio <= 1.3
        width_delta_px = float(detected_roi.w - fitted_roi.w) if comparable_width else 0.0
        within_shift = abs(shift_x) <= max_shift_px if max_shift_px > 0.0 else True
        within_width = abs(width_delta_px) <= max_width_delta_px if (max_width_delta_px > 0.0 and comparable_width) else True

        if auto_correct_enabled and within_shift and within_width:
            new_x = int(round(fitted_roi.x + shift_x))
            new_x = max(0, min(new_x, max(0, frame_w - fitted_roi.w)))
            effective_roi = ROI(x=new_x, y=fitted_roi.y, w=fitted_roi.w, h=fitted_roi.h)
            auto_corrected = abs(new_x - fitted_roi.x) > 0
        else:
            if max_shift_px > 0.0 and not within_shift:
                warning_parts.append(f"ROI drift X {shift_x:+.1f}px")
            if max_width_delta_px > 0.0 and comparable_width and not within_width:
                warning_parts.append(f"ROI width {width_delta_px:+.1f}px")
    elif auto_correct_enabled:
        warning_parts.append("ROI auto no detectada")

    info = RuntimeROIInfo(
        frame_w=frame_w,
        frame_h=frame_h,
        saved_roi=saved_roi,
        effective_roi=effective_roi,
        detected_roi=detected_roi,
        shift_x=shift_x,
        width_delta_px=width_delta_px,
        auto_corrected=auto_corrected,
        warning=" | ".join(warning_parts),
    )
    return effective_roi, info
