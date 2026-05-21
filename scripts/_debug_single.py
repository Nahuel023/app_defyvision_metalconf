"""Quick single-frame detection debug."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cv2
import numpy as np
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.utils.config import load_tolerances

model = "modelo_B"
tol = load_tolerances(model)
roi = load_roi(model, "scanner_1")
pattern = load_pattern(find_pattern_path(model, "scanner_1"))

print(f"has_grid={pattern.has_grid}  n_points={len(pattern.points)}  dx={pattern.dx}  dy={pattern.dy}")
print(f"tol_xy_px={tol['tol_xy_px']}  min_area={tol['min_area']}  threshold={tol['threshold']}")
print(f"use_clahe={tol.get('use_clahe')}  use_otsu={tol.get('use_otsu')}  polarity={tol['polarity']}")
print(f"roi={roi}")
print()

img_full = cv2.imread("C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741/frame_0001.png")
print(f"img_full shape: {img_full.shape}")
img_al, res = align_image_by_right_edge(img_full)
img = apply_roi(img_al, roi) if roi else img_al
print(f"img after roi shape: {img.shape}")

mask = preprocess_for_holes(
    img,
    threshold=int(tol["threshold"]),
    use_channel=str(tol["use_channel"]),
    polarity=str(tol["polarity"]),
    use_clahe=bool(tol.get("use_clahe", False)),
    clahe_clip=float(tol.get("clahe_clip", 2.0)),
    clahe_tile=int(tol.get("clahe_tile", 8)),
    use_otsu=bool(tol.get("use_otsu", False)),
)
white_pct = mask.sum() / (255 * mask.size) * 100
print(f"mask: white={white_pct:.1f}%")
cv2.imwrite("data/debug_single_mask.png", mask)

holes = detect_holes_from_mask(
    mask,
    min_area=float(tol["min_area"]),
    circularity_min=float(tol["circularity_min"]),
    aspect_ratio_max=float(tol["aspect_ratio_max"]),
    edge_margin_px=float(tol.get("edge_margin_px", 0.0)),
)
print(f"holes detected: {len(holes)}")
if holes:
    areas = [h.area for h in holes]
    print(f"areas: min={min(areas):.0f}  max={max(areas):.0f}  median={np.median(areas):.0f}")
