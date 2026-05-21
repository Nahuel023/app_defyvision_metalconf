"""Debug frame_0001 grid matching detail."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import cv2, numpy as np
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.grid_fitting import grid_compare_points, estimate_phase
from src.utils.config import load_tolerances

model = "modelo_B"
tol = load_tolerances(model)
roi = load_roi(model, None)
pattern = load_pattern(find_pattern_path(model, None))
dx, dy = pattern.dx, pattern.dy
phase_ref_x, phase_ref_y = pattern.phase_x, pattern.phase_y

print(f"dx={dx} dy={dy} phase_ref_x={phase_ref_x} phase_ref_y={phase_ref_y}")

for fname in ["frame_0001.png", "frame_0050.png"]:
    img_full = cv2.imread(f"C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741/{fname}")
    img_al, _ = align_image_by_right_edge(img_full)
    img = apply_roi(img_al, roi) if roi else img_al
    h, w = img.shape[:2]

    mask = preprocess_for_holes(img,
        threshold=int(tol["threshold"]), use_channel=str(tol["use_channel"]),
        polarity=str(tol["polarity"]),
        use_clahe=bool(tol.get("use_clahe", False)), clahe_clip=float(tol.get("clahe_clip", 2.0)),
        clahe_tile=int(tol.get("clahe_tile", 8)), use_otsu=bool(tol.get("use_otsu", False))
    )
    holes = detect_holes_from_mask(mask,
        min_area=float(tol["min_area"]), circularity_min=float(tol["circularity_min"]),
        aspect_ratio_max=float(tol["aspect_ratio_max"]),
        edge_margin_px=float(tol.get("edge_margin_px", 0.0))
    )
    det_xy = np.array([(h.x, h.y) for h in holes], dtype=np.float32)

    # What estimate_phase returns
    ep_x = estimate_phase(det_xy[:, 0], dx)
    ep_y = estimate_phase(det_xy[:, 1], dy)

    # X-phase scan
    ci_arr = np.array([ci for ci, _ in pattern.cells], dtype=np.float32)
    tol_x = max(dx * 0.45, 4.0)
    best_px, best_count_x = 0.0, -1
    for px_c in np.arange(0.0, dx, 1.0):
        exp_xs = px_c + ci_arr * dx
        valid = (exp_xs >= 25) & (exp_xs <= w - 25)
        if not valid.any(): continue
        d = np.abs(det_xy[:, 0:1] - exp_xs[valid][None, :])
        c = int((d.min(axis=1) <= tol_x).sum())
        if c > best_count_x: best_count_x, best_px = c, px_c

    # Y-phase scan
    cj_arr = np.array([cj for _, cj in pattern.cells], dtype=np.float32)
    tol_y = max(dy * 0.45, 4.0)
    best_py, best_count_y = 0.0, -1
    for py_c in np.arange(0.0, dy, 1.0):
        exp_ys = py_c + cj_arr * dy
        valid = (exp_ys >= 25) & (exp_ys <= h - 25)
        if not valid.any(): continue
        d = np.abs(det_xy[:, 1:2] - exp_ys[valid][None, :])
        c = int((d.min(axis=1) <= tol_y).sum())
        if c > best_count_y: best_count_y, best_py = c, py_c

    compare_pts = grid_compare_points(det_xy, pattern.cells, dx, dy,
        phase_ref_x, phase_ref_y, None, w, h, 25.0)
    exp_arr = np.array(compare_pts, dtype=np.float32)
    if len(exp_arr) > 0 and len(det_xy) > 0:
        # For each expected, find nearest detected
        diff2 = ((exp_arr[:, None, :] - det_xy[None, :, :]) ** 2).sum(axis=2)
        min_dist = np.sqrt(diff2.min(axis=1))
        matched12 = int((min_dist <= 12).sum())
        matched28 = int((min_dist <= 28).sum())
        print(f"\n{fname}: holes={len(holes)} expected={len(compare_pts)}")
        print(f"  estimate_phase: x={ep_x:.1f} y={ep_y:.1f}")
        print(f"  X-scan: best_px={best_px:.1f} count={best_count_x}")
        print(f"  Y-scan: best_py={best_py:.1f} count={best_count_y}")
        print(f"  Nearest-det dist: min={min_dist.min():.1f}  median={np.median(min_dist):.1f}  max={min_dist.max():.1f}")
        print(f"  Matched@tol12: {matched12}/{len(compare_pts)}  @tol28: {matched28}/{len(compare_pts)}")
        hist = np.histogram(min_dist, bins=[0,5,10,15,20,25,30,50,1000])[0]
        print(f"  Dist hist: 0-5:{hist[0]}  5-10:{hist[1]}  10-15:{hist[2]}  15-20:{hist[3]}  20-25:{hist[4]}  25-30:{hist[5]}  30-50:{hist[6]}")
