import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
import cv2
import numpy as np
from pathlib import Path
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi, apply_roi

MODEL = "modelo_A"
SCANNER = "scanner_2"
FRAME = Path(r'C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0162.png')

pat = load_pattern(find_pattern_path(MODEL, SCANNER))
print(f"Pattern: dx={pat.dx}, dy={pat.dy}, stagger={pat.stagger_x_odd}")
print(f"cells: {len(pat.cells)}, points: {len(pat.points)}")

cells = pat.cells
ci_arr = np.array([ci for ci, _ in cells])
cj_arr = np.array([cj for _, cj in cells])
parity = (cj_arr % 2).astype(np.float32)
stagger = pat.stagger_x_odd or 0.0
dx = pat.dx
dy = pat.dy

even_pts = [pat.points[i] for i,(_,cj) in enumerate(cells) if cj%2==0]
odd_pts  = [pat.points[i] for i,(_,cj) in enumerate(cells) if cj%2==1]
even_xs = np.array([p[0] for p in even_pts])
odd_xs  = np.array([p[0] for p in odd_pts])
print(f"Even-cj (small) X: {even_xs.min():.1f}-{even_xs.max():.1f}")
print(f"Odd-cj  (large) X: {odd_xs.min():.1f}-{odd_xs.max():.1f}")

# distinct cj values
cj_vals = sorted(set(cj_arr))
print(f"CJ values: {cj_vals[:10]} ...")

img_full = cv2.imread(str(FRAME))
roi = load_roi(MODEL, SCANNER)
img = apply_roi(img_full, roi) if roi else img_full
img_h, img_w = img.shape[:2]

mask = preprocess_for_holes(img, threshold=175, use_channel='r', polarity='bright',
    use_clahe=True, clahe_clip=3.0, clahe_tile=8, use_otsu=True)
holes = detect_holes_from_mask(mask, min_area=150, circularity_min=0.55,
    aspect_ratio_max=2.5, edge_margin_px=5.0)
det = np.array([(h.x, h.y) for h in holes], dtype=np.float32)
print(f"Detected: {len(holes)}")

tol_x = max(dx * 0.45, 4.0)
print(f"tol_x={tol_x:.1f}, tol_xy_px=18")

best_px, best_count = 0.0, -1
for px_cand in np.arange(0.0, dx, 1.0):
    origins = (px_cand + parity * stagger) % dx
    exp_xs = origins + ci_arr * dx
    valid = (exp_xs >= 5.0) & (exp_xs <= img_w - 5.0)
    if not valid.any():
        continue
    ev = exp_xs[valid]
    diffs = np.abs(det[:,0:1] - ev[None,:])
    count = int((diffs.min(axis=1) <= tol_x).sum())
    if count > best_count:
        best_count = count
        best_px = px_cand

origin_even = best_px
origin_odd = (best_px + stagger) % dx
print(f"Best X-phase: {best_px:.0f} (count={best_count})")
print(f"origin_even={origin_even:.1f} origin_odd={origin_odd:.1f}")

print("\nExpected vs actual:")
min_cj = min(cj_vals)
for cj_check in cj_vals[:4]:
    row_cells = [(ci,cj) for ci,cj in cells if cj==cj_check]
    if not row_cells: continue
    orig = origin_even if cj_check%2==0 else origin_odd
    row_type = "even/small" if cj_check%2==0 else "odd/large"
    exps = [orig + ci*dx for ci,_ in row_cells]
    print(f"  cj={cj_check} ({row_type}): exp_x={[f'{e:.0f}' for e in sorted(exps)]}")
    # Find actual holes near these expected Y
    exp_y = origin_even + cj_check * dy  # approximate
    near_holes = [(h.x, h.y) for h in holes if abs(h.y - exp_y) < 25]
    print(f"    actual X near y={exp_y:.0f}: {sorted([h[0] for h in near_holes])}")
