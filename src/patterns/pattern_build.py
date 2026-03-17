from pathlib import Path

from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import Pattern, save_pattern, pattern_path
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
    tolerances = load_tolerances()
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
    )
    holes = detect_holes_from_mask(
        mask,
        min_area=min_area,
        circularity_min=circularity_min,
        aspect_ratio_max=aspect_ratio_max,
    )

    points = [(h_.x, h_.y) for h_ in holes]
    radii = [h_.r for h_ in holes]

    pat = Pattern(model=model, image_size=(w, h), points=points, radii=radii)
    out = pattern_path(model)
    save_pattern(pat, out)
    return out
