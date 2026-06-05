"""Diagnose per-band boundary X std for modelo_B (microperforado)."""
import cv2, numpy as np, json, sys
sys.path.insert(0, '.')
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.edge_centering import _pattern_bounds_by_band
from src.utils.config import load_tolerances

tols = load_tolerances('modelo_B')
with open('data/patterns/modelo_B/roi.json') as f:
    roi = json.load(f)

img_path = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-MICROPERFORADO_1\frame_0010.png'
img = cv2.imread(img_path)
rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
img_roi = img[ry:ry+rh, rx:rx+rw]

mask = preprocess_for_holes(img_roi,
    threshold=int(tols.get('threshold', 120)),
    use_channel=str(tols.get('use_channel', 'r')),
    polarity=str(tols.get('polarity', 'bright')),
    use_adaptive=bool(tols.get('use_adaptive', False)),
    blur_ksize=int(tols.get('blur_ksize', 1)),
    open_ksize=int(tols.get('open_ksize', 1)),
    close_ksize=int(tols.get('close_ksize', 1)),
)
holes = detect_holes_from_mask(mask,
    min_area=float(tols.get('min_area', 15.0)),
    circularity_min=float(tols.get('circularity_min', 0.2)),
    aspect_ratio_max=float(tols.get('aspect_ratio_max', 3.5)),
    edge_margin_px=float(tols.get('edge_margin_px', 0.0)),
)
print(f"Detected: {len(holes)}")
print(f"X range: {min(h.x for h in holes):.1f}-{max(h.x for h in holes):.1f}")
print(f"r range: {min(h.r for h in holes):.1f}-{max(h.r for h in holes):.1f}")

# Left boundary candidates
left_bounds = sorted(h.x - h.r for h in holes)
global_left = left_bounds[0]
global_right = max(h.x + h.r for h in holes)
print(f"\nglobal_left={global_left:.1f}  global_right={global_right:.1f}")
print(f"\nSmallest 20 (x-r) values:")
for v in left_bounds[:20]:
    print(f"  {v:.1f}")

# Gap analysis
gaps = [(left_bounds[i+1]-left_bounds[i], left_bounds[i], left_bounds[i+1])
        for i in range(min(30, len(left_bounds)-1))]
big_gaps = [(g,a,b) for g,a,b in gaps if g > 4]
print("\nGaps > 4px in leftmost bounds:")
for g,a,b in big_gaps[:5]:
    print(f"  gap={g:.1f}: {a:.1f} -> {b:.1f}")

N_BANDS = int(tols.get('edge_centering_bands', 24))
current_tol = float(tols.get('pattern_edge_boundary_tol_px', 0.0))
print(f"\n--- Current: boundary_tol_px={current_tol}, bands={N_BANDS} ---")

for tol in [current_tol, 5.0, 8.0, 10.0, 15.0]:
    pat_left, pat_right = _pattern_bounds_by_band(holes, rh, n_bands=N_BANDS, min_holes=1, boundary_tol_px=tol)
    lxs = [v[0] for v in pat_left.values()]
    rxs = [v[0] for v in pat_right.values()]
    if lxs and rxs:
        print(f"  tol={tol:4.1f}: LEFT n={len(lxs):2d} range={min(lxs):.1f}-{max(lxs):.1f} std={np.std(lxs):.2f} | RIGHT n={len(rxs):2d} range={min(rxs):.1f}-{max(rxs):.1f} std={np.std(rxs):.2f}")
    else:
        print(f"  tol={tol:4.1f}: insufficient data")
