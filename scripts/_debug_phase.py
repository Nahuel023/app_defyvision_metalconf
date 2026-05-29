"""Muestra la distancia real entre cada posición esperada y el detectado más cercano."""
import numpy as np
from pathlib import Path
from src.utils.config import load_tolerances
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.grid_fitting import grid_compare_points
from src.io.load_images import load_bgr_image

FRAME = "C:/Users/DefyC/Downloads/Patron_Esterilla_METALCONF/frame_0074.png"

img  = load_bgr_image(FRAME)
tols = load_tolerances("modelo_A")
roi  = load_roi("modelo_A", "scanner_2")
pat  = load_pattern(find_pattern_path("modelo_A", "scanner_2"))

img_aligned, _ = align_image_by_right_edge(img)
img_roi = apply_roi(img_aligned, roi) if roi else img_aligned

mask = preprocess_for_holes(
    img_roi,
    threshold=int(tols["threshold"]),
    use_channel=tols.get("use_channel", "r"),
    polarity=tols.get("polarity", "bright"),
    use_clahe=bool(tols.get("use_clahe", False)),
    blur_ksize=int(tols.get("blur_ksize", 5)),
    open_ksize=int(tols.get("open_ksize", 3)),
    close_ksize=int(tols.get("close_ksize", 5)),
)
holes = detect_holes_from_mask(
    mask,
    min_area=float(tols["min_area"]),
    circularity_min=float(tols["circularity_min"]),
    aspect_ratio_max=float(tols["aspect_ratio_max"]),
    edge_margin_px=float(tols["edge_margin_px"]),
)

# Per-type filter
split = float(tols.get("hole_type_split_area", 1000.0))
filtered = []
for h in holes:
    ht = "small" if h.area < split else "large"
    lo = float(tols.get(f"min_area_{ht}", tols["min_area"]))
    hi = float(tols.get(f"max_area_{ht}", 0))
    if h.area < lo or (hi > 0 and h.area > hi):
        continue
    filtered.append(h)
holes = filtered

det = np.array([(h.x, h.y) for h in holes], dtype=np.float32)
img_h, img_w = img_roi.shape[:2]

stagger = float(pat.stagger_x_odd) if pat.stagger_x_odd else 0.0
compare_pts, cells = grid_compare_points(
    det,
    pat.cells, pat.dx, pat.dy, pat.phase_x, pat.phase_y,
    None, img_w, img_h,
    float(tols["edge_margin_px"]),
    tol_affine=float(tols["tol_xy_px"]) * 1.5 if tols.get("grid_affine_refinement") else 0.0,
    stagger_x_odd=stagger,
)

tol = float(tols["tol_xy_px"])
exp = np.array(compare_pts, dtype=np.float32)  # (N, 2)

print(f"Detectados: {len(det)}  |  Esperados (grid): {len(exp)}")
print(f"tol_xy_px = {tol:.1f}  stagger_x_odd = {stagger:.1f}")
print()

# Para cada esperado, distancia al detectado más cercano
dists_to_nearest = []
for ex, ey in exp:
    if len(det) == 0:
        dists_to_nearest.append(9999)
        continue
    d = np.sqrt((det[:,0]-ex)**2 + (det[:,1]-ey)**2)
    dists_to_nearest.append(float(d.min()))

dists = np.array(dists_to_nearest)
matched = (dists <= tol).sum()
missing = (dists > tol).sum()

print(f"Matched (dist <= {tol}px): {matched}")
print(f"Missing (dist >  {tol}px): {missing}")
print()

bins = [0, 5, 10, 15, 18, 25, 35, 50, 9999]
labels = ["0-5", "5-10", "10-15", "15-18", "18-25", "25-35", "35-50", ">50"]
print("Distancias esperado -> detectado mas cercano:")
for i in range(len(labels)):
    n = int(((dists >= bins[i]) & (dists < bins[i+1])).sum())
    bar = "#" * n
    print(f"  {labels[i]:8s}: {n:3d}  {bar}")

print()
print("Top 15 esperados con mayor distancia al detectado más cercano:")
idx_sorted = np.argsort(dists)[::-1][:15]
for i in idx_sorted:
    ex, ey = exp[i]
    d = dists[i]
    # ci/cj de esta celda
    cell = cells[i] if i < len(cells) else ("?","?")
    print(f"  ci={cell[0]:2} cj={cell[1]:2}  expected=({ex:.0f},{ey:.0f})  nearest_dist={d:.1f}px  {'MISSING' if d>tol else 'ok'}")
