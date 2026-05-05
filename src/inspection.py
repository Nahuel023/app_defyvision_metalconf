from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from src.io.load_images import load_bgr_image
from src.io.save_results import save_image
from src.patterns.pattern_io import load_pattern, pattern_path
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.annotate import draw_compare_overlay
from src.pipeline.compare import CompareReport, compare_missing_only
from src.pipeline.detect_holes import Hole, detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class InspectionResult:
    model: str
    image_path: Path
    status: str
    report: CompareReport
    holes: list[Hole]
    mask: np.ndarray
    overlay: np.ndarray
    angle_deg: float
    used_lines: int
    shift_xy: tuple[float, float] | None


@dataclass(frozen=True)
class FolderInspectionSummary:
    model: str
    input_dir: Path
    total: int
    ok: int
    nok: int
    results: list[InspectionResult]
    temporal_ok: int
    temporal_nok: int
    temporal_results: list["TemporalFrameResult"]
    consecutive_nok_frames: int
    frame_rate_hz: float
    max_response_sec: float
    response_time_sec: float
    meets_response_target: bool


@dataclass(frozen=True)
class TemporalFrameResult:
    result: InspectionResult
    nok_streak: int
    decision_status: str
    triggered: bool


def iter_image_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            yield path


def inspect_image(model: str, img_path: Path, save: bool = False) -> InspectionResult:
    """Inspect an image from disk."""
    img_full = load_bgr_image(img_path)
    result = _inspect_bgr(model, img_full, image_path=img_path)
    if save:
        _save_result_images(result)
    return result


def inspect_frame(
    model: str,
    frame: np.ndarray,
    frame_id: str = "live",
    save: bool = False,
) -> InspectionResult:
    """Inspect a BGR frame captured from a live camera (no disk read)."""
    result = _inspect_bgr(model, frame, image_path=Path(frame_id))
    if save:
        _save_result_images(result)
    return result


def _inspect_bgr(model: str, img_full: np.ndarray, image_path: Path) -> InspectionResult:
    """Core inspection logic on a pre-loaded BGR frame."""
    tolerances = load_tolerances()
    threshold = int(tolerances["threshold"])
    use_channel = str(tolerances["use_channel"])
    polarity = str(tolerances["polarity"])
    min_area = float(tolerances["min_area"])
    circularity_min = float(tolerances["circularity_min"])
    tol_xy_px = float(tolerances["tol_xy_px"])
    aspect_ratio_max = float(tolerances["aspect_ratio_max"])
    align_match_tol_px = float(tolerances["align_match_tol_px"])
    min_match_count = int(tolerances["min_match_count"])

    pattern = load_pattern(pattern_path(model))

    img_aligned, align_res = align_image_by_right_edge(img_full)

    roi = load_roi(model)
    img = apply_roi(img_aligned, roi) if roi is not None else img_aligned

    mask0 = preprocess_for_holes(img, threshold=threshold,
                                  use_channel=use_channel, polarity=polarity)
    holes0 = detect_holes_from_mask(mask0, min_area=min_area,
                                     circularity_min=circularity_min,
                                     aspect_ratio_max=aspect_ratio_max)

    shift_xy: tuple[float, float] | None = None
    transform = _estimate_alignment_transform(
        pattern.points, holes0,
        match_tol_px=align_match_tol_px, min_match_count=min_match_count,
    )
    if transform is not None:
        shift_xy = (float(transform[0, 2]), float(transform[1, 2]))
        img = cv2.warpAffine(img, transform, (img.shape[1], img.shape[0]),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    mask = preprocess_for_holes(img, threshold=threshold,
                                 use_channel=use_channel, polarity=polarity)
    holes = detect_holes_from_mask(mask, min_area=min_area,
                                    circularity_min=circularity_min,
                                    aspect_ratio_max=aspect_ratio_max)
    detected_points = [(h.x, h.y) for h in holes]
    report = compare_missing_only(pattern.points, detected_points, tol_xy_px=tol_xy_px)
    overlay = draw_compare_overlay(img, holes, report.missing_points, report.status)

    return InspectionResult(
        model=model,
        image_path=image_path,
        status=report.status,
        report=report,
        holes=holes,
        mask=mask,
        overlay=overlay,
        angle_deg=float(align_res.angle_deg),
        used_lines=int(align_res.used_lines),
        shift_xy=shift_xy,
    )


def inspect_folder(
    model: str,
    input_dir: Path,
    save: bool = False,
    frame_rate_hz: float | None = None,
    consecutive_nok_frames: int | None = None,
    max_response_sec: float | None = None,
) -> FolderInspectionSummary:
    tolerances = load_tolerances()
    frame_rate_hz = float(
        tolerances["frame_rate_hz"] if frame_rate_hz is None else frame_rate_hz
    )
    consecutive_nok_frames = int(
        tolerances["consecutive_nok_frames"]
        if consecutive_nok_frames is None
        else consecutive_nok_frames
    )
    max_response_sec = float(
        tolerances["max_response_sec"] if max_response_sec is None else max_response_sec
    )

    image_paths = list(iter_image_files(input_dir))
    results = [inspect_image(model, path, save=save) for path in image_paths]
    ok_count = sum(1 for result in results if result.status == "OK")
    temporal_results = _apply_temporal_rule(results, consecutive_nok_frames)
    temporal_ok = sum(1 for item in temporal_results if item.decision_status == "OK")
    response_time_sec = (
        float("inf") if frame_rate_hz <= 0 else consecutive_nok_frames / frame_rate_hz
    )
    return FolderInspectionSummary(
        model=model,
        input_dir=input_dir,
        total=len(results),
        ok=ok_count,
        nok=len(results) - ok_count,
        results=results,
        temporal_ok=temporal_ok,
        temporal_nok=len(temporal_results) - temporal_ok,
        temporal_results=temporal_results,
        consecutive_nok_frames=consecutive_nok_frames,
        frame_rate_hz=frame_rate_hz,
        max_response_sec=max_response_sec,
        response_time_sec=response_time_sec,
        meets_response_target=response_time_sec <= max_response_sec,
    )


def _save_result_images(result: InspectionResult) -> None:
    out_dir = Path("data/output/ok") if result.status == "OK" else Path("data/output/nok")
    dbg_dir = Path("data/output/debug")
    save_image(dbg_dir / f"{result.image_path.stem}_mask.png", result.mask)
    save_image(out_dir / f"{result.image_path.stem}_overlay.png", result.overlay)


def _estimate_alignment_transform(
    pattern_points: list[tuple[float, float]],
    detected_holes: list[Hole],
    match_tol_px: float,
    min_match_count: int,
) -> np.ndarray | None:
    if len(pattern_points) < min_match_count or len(detected_holes) < min_match_count:
        return None

    det_np = np.array([(h.x, h.y) for h in detected_holes], dtype=np.float32)
    pat_np = np.array(pattern_points, dtype=np.float32)

    c_det = det_np.mean(axis=0)
    c_pat = pat_np.mean(axis=0)
    shifted_det = det_np + (c_pat - c_det)

    src_points: list[np.ndarray] = []
    dst_points: list[np.ndarray] = []
    used_pat_idx: set[int] = set()

    for det_raw, det_shifted in zip(det_np, shifted_det):
        distances = np.linalg.norm(pat_np - det_shifted, axis=1)
        best_idx = int(np.argmin(distances))
        if distances[best_idx] > match_tol_px or best_idx in used_pat_idx:
            continue
        used_pat_idx.add(best_idx)
        src_points.append(det_raw)
        dst_points.append(pat_np[best_idx])

    if len(src_points) < min_match_count:
        return None

    affine, _ = cv2.estimateAffinePartial2D(
        np.array(src_points, dtype=np.float32),
        np.array(dst_points, dtype=np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=max(3.0, match_tol_px * 0.25),
        maxIters=2000,
        confidence=0.99,
    )
    return affine


def _apply_temporal_rule(
    results: list[InspectionResult],
    consecutive_nok_frames: int,
) -> list[TemporalFrameResult]:
    streak = 0
    temporal_results: list[TemporalFrameResult] = []
    for result in results:
        if result.status == "NOK":
            streak += 1
        else:
            streak = 0

        decision_status = "NOK" if streak >= consecutive_nok_frames else "OK"
        temporal_results.append(
            TemporalFrameResult(
                result=result,
                nok_streak=streak,
                decision_status=decision_status,
                triggered=decision_status == "NOK",
            )
        )
    return temporal_results
