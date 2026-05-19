"""Debug grid_compare_points for a specific frame."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json, numpy as np
from pathlib import Path
import cv2

from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.grid_fitting import estimate_phase, estimate_spacing, grid_compare_points
from src.utils.config import load_tolerances

model = "modelo_B"
scanner_id = "scanner_1"
img_path = Path("data/recordings/20260513_160724/frame_0019.png")

tol = load_tolerances(model)
pattern = load_pattern(find_pattern_path(model, scanner_id))

img_full = cv2.imread(str(img_path))
img_aligned, _ = align_image_by_right_edge(img_full)
roi = load_roi(model, scanner_id)
img = apply_roi(img_aligned, roi) if roi is not None else img_aligned
img_h, img_w = img.shape[:2]
print(f"ROI image: {img_w}x{img_h}")

mask = preprocess_for_holes(img, threshold=int(tol["threshold"]),
    use_channel=str(tol["use_channel"]), polarity=str(tol["polarity"]),
    use_clahe=bool(tol.get("use_clahe",False)), clahe_clip=float(tol.get("clahe_clip",2.0)),
    clahe_tile=int(tol.get("clahe_tile",8)), use_otsu=bool(tol.get("use_otsu",False)))
holes = detect_holes_from_mask(mask, min_area=float(tol["min_area"]),
    circularity_min=float(tol["circularity_min"]),
    aspect_ratio_max=float(tol.get("aspect_ratio_max",2.0)),
    edge_margin_px=float(tol.get("edge_margin_px",0.0)))

print(f"Detected holes: {len(holes)}")
det_arr = np.array([(h.x, h.y) for h in holes], dtype=np.float32)

print(f"\nPattern grid: dx={pattern.dx}  dy={pattern.dy}  phase_x={pattern.phase_x}  phase_y={pattern.phase_y}")
print(f"Pattern cells count: {len(pattern.cells)}")
print(f"Pattern cells ci range: {min(c[0] for c in pattern.cells)} to {max(c[0] for c in pattern.cells)}")
print(f"Pattern cells cj range: {min(c[1] for c in pattern.cells)} to {max(c[1] for c in pattern.cells)}")

# Estimate phase from detected holes
phase_x_det = estimate_phase(det_arr[:, 0], pattern.dx)
phase_y_det = estimate_phase(det_arr[:, 1], pattern.dy)
print(f"\nDetected phase_x={phase_x_det:.1f}  (pattern phase_x={pattern.phase_x:.1f})")
print(f"Detected phase_y={phase_y_det:.1f}  (pattern phase_y={pattern.phase_y:.1f})")

# Compute expected positions with k=0
margin = float(tol.get("edge_margin_px", 0.0))
compare_points = grid_compare_points(det_arr, pattern.cells,
    pattern.dx, pattern.dy, pattern.phase_x, pattern.phase_y,
    None, img_w, img_h, margin)
print(f"\nExpected positions (compare_points): {len(compare_points)}")

# How many compare_points have a nearby detected hole?
if compare_points and len(det_arr) > 0:
    matched = 0
    tol_xy = float(tol["tol_xy_px"])
    for ex, ey in compare_points:
        dists = np.sqrt(((det_arr - [ex, ey])**2).sum(axis=1))
        if dists.min() < tol_xy:
            matched += 1
    print(f"Matched: {matched}/{len(compare_points)} (tol={tol_xy}px)")

# Show first 10 compare_points vs nearest detected
print("\nFirst 10 expected vs nearest detected:")
for ex, ey in list(compare_points)[:10]:
    if len(det_arr) > 0:
        dists = np.sqrt(((det_arr - [ex, ey])**2).sum(axis=1))
        idx = int(dists.argmin())
        dx, dy = det_arr[idx]
        dist = dists[idx]
        print(f"  expected=({ex:.1f},{ey:.1f})  nearest_det=({dx:.1f},{dy:.1f})  dist={dist:.1f}px")
