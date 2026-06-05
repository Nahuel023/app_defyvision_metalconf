"""Diagnostic: per-band left/right edge X positions for esterilla."""
import cv2, numpy as np, json, sys
sys.path.insert(0, '.')
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.edge_centering import _pattern_bounds_by_band
from src.utils.config import load_tolerances

tol_config = load_tolerances('modelo_A')
with open('data/patterns/modelo_A/roi.json') as f:
    roi = json.load(f)

img_path = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-ESTERILLA_1\frame_0010.png'
img = cv2.imread(img_path)
rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
img_roi = img[ry:ry+rh, rx:rx+rw]

mask = preprocess_for_holes(img_roi,
    threshold=int(tol_config.get('threshold', 180)),
    use_channel=str(tol_config.get('use_channel', 'gray')),
    polarity=str(tol_config.get('polarity', 'bright')),
    use_adaptive=bool(tol_config.get('use_adaptive', False)),
    adaptive_block_size=int(tol_config.get('adaptive_block_size', 41)),
    adaptive_c=float(tol_config.get('adaptive_c', -5.0)),
    blur_ksize=int(tol_config.get('blur_ksize', 5)),
    open_ksize=int(tol_config.get('open_ksize', 1)),
    close_ksize=int(tol_config.get('close_ksize', 3)),
)

holes = detect_holes_from_mask(mask,
    min_area=float(tol_config.get('min_area', 20.0)),
    circularity_min=float(tol_config.get('circularity_min', 0.15)),
    aspect_ratio_max=float(tol_config.get('aspect_ratio_max', 4.5)),
    edge_margin_px=float(tol_config.get('edge_margin_px', 0.0)),
)

print(f"Detected (with edge_margin): {len(holes)}")

# Simulate per-band boundaries
N_BANDS = 16
boundary_tol = float(tol_config.get('pattern_edge_boundary_tol_px', 0.0))
pat_left, pat_right = _pattern_bounds_by_band(holes, rh, n_bands=N_BANDS, min_holes=1, boundary_tol_px=boundary_tol)

left_xs = [v[0] for v in pat_left.values()]
right_xs = [v[0] for v in pat_right.values()]

print(f"\nboundary_tol_px = {boundary_tol}")
print(f"LEFT  bands: {len(left_xs)}  x-range: {min(left_xs):.1f}-{max(left_xs):.1f}  std={np.std(left_xs):.2f}")
print(f"RIGHT bands: {len(right_xs)}  x-range: {min(right_xs):.1f}-{max(right_xs):.1f}  std={np.std(right_xs):.2f}")
print(f"\nLeft per-band x values: {[f'{x:.1f}' for x in sorted(left_xs)]}")
print(f"Right per-band x values: {[f'{x:.1f}' for x in sorted(right_xs, reverse=True)]}")
