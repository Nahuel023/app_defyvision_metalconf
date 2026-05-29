"""Debug updated grid_compare_points with tie-breaking."""
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
cells = pat.cells
ci_arr = np.array([ci for ci, _ in cells])
cj_arr = np.array([cj for _, cj in cells])
parity = (cj_arr % 2).astype(np.float32)
stagger = pat.stagger_x_odd or 0.0
dx, dy = pat.dx, pat.dy

img_full = cv2.imread(str(FRAME))
roi = load_roi(MODEL, SCANNER)
img = apply_roi(img_full, roi) if roi else img_full
img_h, img_w = img.shape[:2]

mask = preprocess_for_holes(img, threshold=175, use_channel='r', polarity='bright',
    use_clahe=True, clahe_clip=3.0, clahe_tile=8, use_otsu=True)
holes = detect_holes_from_mask(mask, min_area=150, circularity_min=0.55,
    aspect_ratio_max=2.5, edge_margin_px=5.0)
det = np.array([(h.x, h.y) for h in holes], dtype=np.float32)

tol_x = max(dx * 0.45, 4.0)
det_xs = det[:, 0]
margin = 5.0

# X-phase scan with tie-breaking
best_px = 0.0
best_count = -1
best_dist = float("inf")
results = []
for px_cand in np.arange(0.0, dx, 1.0):
    origins = (px_cand + parity * stagger) % dx
    exp_xs = origins + ci_arr * dx
    valid_x = (exp_xs >= margin) & (exp_xs <= img_w - margin)
    if not valid_x.any():
        continue
    ev = exp_xs[valid_x]
    diffs = np.abs(det_xs[:, None] - ev[None, :])
    min_d = diffs.min(axis=1)
    count = int((min_d <= tol_x).sum())
    total_d = float(min_d[min_d <= tol_x].sum())
    results.append((px_cand, count, total_d))
    if count > best_count or (count == best_count and total_d < best_dist):
        best_count = count
        best_dist = total_d
        best_px = px_cand

print(f"X-phase: best={best_px:.0f} count={best_count} total_dist={best_dist:.1f}")

# Show top 10 by (count, -total_dist)
top = sorted(results, key=lambda r: (-r[1], r[2]))[:10]
print("Top 10 phases (px, count, total_dist):")
for px, cnt, td in top:
    print(f"  px={int(px):2d}: count={cnt} dist={td:.1f}")

origin_even = best_px
origin_odd = (best_px + stagger) % dx
print(f"\norigin_even={origin_even:.0f} origin_odd={origin_odd:.0f}")

# Check expected vs actual for first row
print("\nExpected vs actual positions:")
for cj_check in sorted(set(cj_arr))[:4]:
    orig = origin_even if cj_check % 2 == 0 else origin_odd
    row_cis = [ci for ci, cj in cells if cj == cj_check]
    exp_xs_row = sorted([orig + ci*dx for ci in row_cis])
    print(f"  cj={cj_check} ({'even' if cj_check%2==0 else 'odd'}): exp={[int(x) for x in exp_xs_row]}")
    exp_y = best_px + cj_check * dy  # rough estimate
    near = sorted([h.x for h in holes if abs(h.y - exp_y) < 25])
    print(f"    actual near y={exp_y:.0f}: {[int(x) for x in near]}")

# Check specific phases
print("\n--- Check phases 17 and 40 ---")
for check_px in [17.0, 40.0]:
    origins = (check_px + parity * stagger) % dx
    exp_xs_all = origins + ci_arr * dx
    valid_x = (exp_xs_all >= margin) & (exp_xs_all <= img_w - margin)
    ev = exp_xs_all[valid_x]
    diffs = np.abs(det_xs[:, None] - ev[None, :])
    min_d = diffs.min(axis=1)
    count = int((min_d <= tol_x).sum())
    total_d = float(min_d[min_d <= tol_x].sum())
    print(f"  px={int(check_px)}: count={count}, total_dist={total_d:.1f}")
    print(f"    origin_even={check_px:.0f}, origin_odd={(check_px+stagger)%dx:.0f}")
    o_even = check_px
    o_odd = (check_px + stagger) % dx
    # Print expected for first cells
    for cj_v in sorted(set(cj_arr))[:2]:
        p = cj_v % 2
        orig = o_even if p == 0 else o_odd
        cis = sorted([ci for ci,cj in cells if cj==cj_v])
        exps = [orig + ci*dx for ci in cis]
        print(f"    cj={cj_v}: exp_x={[int(e) for e in sorted(exps)]}")
