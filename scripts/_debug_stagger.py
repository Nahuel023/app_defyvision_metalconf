"""Debug the stagger grid fitting for Esterilla."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import cv2
import numpy as np
from pathlib import Path
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.grid_fitting import estimate_phase, grid_compare_points

MODEL = "modelo_A"
SCANNER = "scanner_2"
FRAME = Path(r'C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0162.png')

# Load pattern
pat_path = find_pattern_path(MODEL, SCANNER)
pat = load_pattern(pat_path)
print(f"Pattern: {len(pat.points)} pts, dx={pat.dx}, dy={pat.dy}")
print(f"  phase_x={pat.phase_x}, phase_y={pat.phase_y}")
print(f"  stagger_x_odd={pat.stagger_x_odd}")
print(f"  cells count={len(pat.cells) if pat.cells else 0}")

# Load and preprocess frame
img_full = cv2.imread(str(FRAME))
roi = load_roi(MODEL, SCANNER)
print(f"\nROI: {roi}")
if roi:
    from src.patterns.roi import apply_roi
    img = apply_roi(img_full, roi)
else:
    img = img_full
img_h, img_w = img.shape[:2]
print(f"Frame size after ROI: {img_w}x{img_h}")

mask = preprocess_for_holes(img, threshold=175, use_channel='r', polarity='bright',
    use_clahe=True, clahe_clip=3.0, clahe_tile=8, use_otsu=True)
holes = detect_holes_from_mask(mask, min_area=150, circularity_min=0.55,
    aspect_ratio_max=2.5, edge_margin_px=5.0)

print(f"\nDetected {len(holes)} holes")
xs = np.array([h.x for h in holes])
ys = np.array([h.y for h in holes])
print(f"X range: {xs.min():.0f} - {xs.max():.0f}")
print(f"Y range: {ys.min():.0f} - {ys.max():.0f}")

# Sort by Y to see rows
holes_sorted = sorted(holes, key=lambda h: h.y)
print("\nFirst 20 holes (sorted by Y):")
print(f"{'y':>7} {'x':>7} {'area':>7}")
for h in holes_sorted[:20]:
    print(f"{h.y:7.1f} {h.x:7.1f} {h.area:7.0f}")

# Check pattern cells vs expected positions
if pat.has_grid:
    print(f"\n--- Grid analysis ---")
    print(f"dx={pat.dx}, dy={pat.dy}, stagger_x_odd={pat.stagger_x_odd}")

    cells = pat.cells
    ci_arr = np.array([ci for ci, _ in cells])
    cj_arr = np.array([cj for _, cj in cells])
    parity = cj_arr % 2
    stagger = pat.stagger_x_odd or 0.0

    # Check X-phase estimation
    ph_x = estimate_phase(xs, pat.dx)
    print(f"X-phase from detected (all): {ph_x:.1f}")

    even_xs = xs[:]  # All xs for now
    # Separate by "row parity" based on actual Y clusters
    ph_even = estimate_phase(xs[ys < 400], pat.dx)  # arbitrary cutoff
    print(f"X-phase from detected (first half): {ph_even:.1f}")

    # Manual check: what does best_phase_x converge to?
    det_arr = np.array([(h.x, h.y) for h in holes], dtype=np.float32)
    tol_x = max(pat.dx * 0.45, 4.0)
    print(f"tol_x = {tol_x:.1f}")

    best_phase = estimate_phase(det_arr[:, 0], pat.dx)
    best_count = -1
    for px in np.arange(0.0, pat.dx, 1.0):
        origins = (px + parity * stagger) % pat.dx
        exp_xs = origins + ci_arr * pat.dx
        valid = (exp_xs >= 5.0) & (exp_xs <= img_w - 5.0)
        if not valid.any():
            continue
        exp_xs_v = exp_xs[valid]
        diffs = np.abs(det_arr[:, 0:1] - exp_xs_v[None, :])
        count = int((diffs.min(axis=1) <= tol_x).sum())
        if count > best_count:
            best_count = count
            best_phase = float(px)

    print(f"Best X-phase: {best_phase:.1f} (count={best_count})")

    # Check what expected positions look like
    origin_even = best_phase
    origin_odd = (best_phase + stagger) % pat.dx
    print(f"origin_even={origin_even:.1f}, origin_odd={origin_odd:.1f}")

    print("\nExpected X positions for first 3 rows in pattern:")
    for cj_check in [0, 1, 2]:
        cells_row = [(i, (ci, cj)) for i, (ci, cj) in enumerate(cells) if cj == cj_check][:6]
        if not cells_row:
            continue
        print(f"  cj={cj_check} ({'even' if cj_check%2==0 else 'odd'}):")
        for i, (ci, cj) in cells_row:
            origin = origin_even if cj % 2 == 0 else origin_odd
            ex = origin + ci * pat.dx
            # Find nearest detected hole
            nearest = min(holes, key=lambda h: abs(h.x - ex))
            print(f"    ci={ci}: exp_x={ex:.1f}, nearest_x={nearest.x:.1f}, diff={abs(nearest.x-ex):.1f}")
