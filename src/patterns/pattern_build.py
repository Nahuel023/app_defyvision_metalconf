from pathlib import Path

from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import Pattern, save_pattern, pattern_path
from src.pipeline.grid_fitting import estimate_spacing, estimate_phase, assign_cells
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances


def build_pattern_from_image(
    model: str,
    img_path: Path,
    threshold: int | None = None,
    min_area: float | None = None,
    circularity_min: float | None = None,
) -> Path:
    tolerances = load_tolerances(model)
    threshold = int(tolerances["threshold"] if threshold is None else threshold)
    min_area = float(tolerances["min_area"] if min_area is None else min_area)
    circularity_min = float(
        tolerances["circularity_min"] if circularity_min is None else circularity_min
    )
    aspect_ratio_max = float(tolerances["aspect_ratio_max"])

    img_full = load_bgr_image(img_path)

    img_aligned, align_res = align_image_by_right_edge(img_full)
    print(f"[align-pattern] angle_deg={align_res.angle_deg:.2f} lines={align_res.used_lines}")

    roi = load_roi(model)
    if roi is not None:
        img = apply_roi(img_aligned, roi)
    else:
        img = img_aligned

    h, w = img.shape[:2]
    mask = preprocess_for_holes(
        img,
        threshold=threshold,
        use_channel=str(tolerances["use_channel"]),
        polarity=str(tolerances["polarity"]),
        use_clahe=bool(tolerances.get("use_clahe", False)),
        clahe_clip=float(tolerances.get("clahe_clip", 2.0)),
        clahe_tile=int(tolerances.get("clahe_tile", 8)),
        use_otsu=bool(tolerances.get("use_otsu", False)),
    )
    # Use pattern_edge_margin_px if set; falls back to edge_margin_px.
    # A larger build margin excludes edge-zone holes that only appear at the
    # reference position and disappear when the sheet shifts.
    build_margin = float(tolerances.get(
        "pattern_edge_margin_px",
        tolerances.get("edge_margin_px", 0.0),
    ))
    holes = detect_holes_from_mask(
        mask,
        min_area=min_area,
        circularity_min=circularity_min,
        aspect_ratio_max=aspect_ratio_max,
        edge_margin_px=build_margin,
    )

    points = [(h_.x, h_.y) for h_ in holes]
    radii = [h_.r for h_ in holes]

    import numpy as np
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    dx = estimate_spacing(xs)
    dy = estimate_spacing(ys)
    phase_x = estimate_phase(xs, dx)
    phase_y = estimate_phase(ys, dy)
    cells = assign_cells(points, dx, dy, phase_x, phase_y)
    print(f"[build-pattern] {len(points)} holes  dx={dx:.1f} dy={dy:.1f}"
          f"  phase=({phase_x:.1f},{phase_y:.1f})")

    pat = Pattern(
        model=model, image_size=(w, h), points=points, radii=radii,
        dx=dx, dy=dy, phase_x=phase_x, phase_y=phase_y, cells=cells,
    )
    out = pattern_path(model)
    save_pattern(pat, out)
    return out
