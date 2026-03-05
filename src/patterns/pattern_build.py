from pathlib import Path
import cv2

from src.io.load_images import load_bgr_image
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.patterns.pattern_io import Pattern, save_pattern, pattern_path
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge


def build_pattern_from_image(
    model: str,
    img_path: Path,
    threshold: int = 90,
    min_area: float = 80,
    circularity_min: float = 0.6,
) -> Path:
    img_full = load_bgr_image(img_path)

    # 1) alinear por borde derecho (siempre)
    img_aligned, align_res = align_image_by_right_edge(img_full)
    print(f"[align-pattern] angle_deg={align_res.angle_deg:.2f} lines={align_res.used_lines}")

    # 2) aplicar ROI si existe
    roi = load_roi(model)
    if roi is not None:
        img = apply_roi(img_aligned, roi)
    else:
        img = img_aligned

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    mask = preprocess_for_holes(gray, threshold=threshold)
    holes = detect_holes_from_mask(mask, min_area=min_area, circularity_min=circularity_min)

    points = [(h_.x, h_.y) for h_ in holes]
    radii = [h_.r for h_ in holes]

    pat = Pattern(model=model, image_size=(w, h), points=points, radii=radii)
    out = pattern_path(model)
    save_pattern(pat, out)
    return out