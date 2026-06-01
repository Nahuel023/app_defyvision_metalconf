"""Diagnose real esterilla geometry on a reference frame (build-equivalent path)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.grid_fitting import estimate_spacing, estimate_phase, assign_cells
from src.utils.config import load_tolerances

MODEL = "modelo_A"
SCANNER = "scanner_2"
img_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0162.png")

tol = load_tolerances(MODEL)
img_full = load_bgr_image(img_path)
img_aligned, ar = align_image_by_right_edge(img_full)
roi = load_roi(MODEL, SCANNER)
img = apply_roi(img_aligned, roi) if roi is not None else img_aligned
h, w = img.shape[:2]
print(f"img {w}x{h}  roi={roi}")

mask = preprocess_for_holes(
    img, threshold=int(tol["threshold"]), use_channel=str(tol["use_channel"]),
    polarity=str(tol["polarity"]), use_clahe=bool(tol.get("use_clahe", False)),
    clahe_clip=float(tol.get("clahe_clip", 2.0)), clahe_tile=int(tol.get("clahe_tile", 8)),
    use_otsu=bool(tol.get("use_otsu", False)), blur_ksize=int(tol.get("blur_ksize", 5)),
    open_ksize=int(tol.get("open_ksize", 3)), close_ksize=int(tol.get("close_ksize", 5)),
)
build_margin = float(tol.get("pattern_edge_margin_px", tol.get("edge_margin_px", 0.0)))
holes = detect_holes_from_mask(
    mask, min_area=float(tol["min_area"]), circularity_min=float(tol["circularity_min"]),
    aspect_ratio_max=float(tol["aspect_ratio_max"]), edge_margin_px=build_margin,
)
pts = [(hh.x, hh.y) for hh in holes]
radii = [hh.r for hh in holes]
xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
areas = np.array([np.pi*hh.r**2 for hh in holes])
print(f"detected={len(pts)}  build_margin={build_margin}")
print(f"radii: min={min(radii):.1f} med={np.median(radii):.1f} max={max(radii):.1f}")

# Real spacing via nearest-neighbor in each axis (mode of diffs)
gms = float(tol.get("grid_min_spacing", 30.0))
print(f"estimate_spacing X={estimate_spacing(xs,gms):.1f}  Y={estimate_spacing(ys,gms):.1f}  (config dx={tol.get('grid_dx')} dy={tol.get('grid_dy')})")

# Real dy: for each hole, nearest hole directly above/below in same column-ish (|dx|<15)
def nn_axis_spacing(coords_main, coords_other, max_other=15.0):
    d = []
    arr_m = coords_main; arr_o = coords_other
    for i in range(len(arr_m)):
        same = np.abs(arr_o - arr_o[i]) < max_other
        same[i] = False
        if same.any():
            dd = np.abs(arr_m[same] - arr_m[i])
            dd = dd[dd > 5]
            if len(dd): d.append(dd.min())
    return np.array(d)

dy_real = nn_axis_spacing(ys, xs)   # holes in same column (similar x) -> y spacing
dx_real = nn_axis_spacing(xs, ys)   # holes in same row (similar y) -> x spacing
print(f"dy_real nearest: med={np.median(dy_real):.2f} mean={dy_real.mean():.2f} n={len(dy_real)}")
print(f"dx_real nearest: med={np.median(dx_real):.2f} mean={dx_real.mean():.2f} n={len(dx_real)}")

# assign cells with config dx/dy
dx = float(tol["grid_dx"]); dy = float(tol["grid_dy"])
phx = estimate_phase(xs, dx); phy = estimate_phase(ys, dy)
cells = assign_cells(pts, dx, dy, phx, phy)
from collections import Counter
c = Counter(cells)
dups = sorted([(k,v) for k,v in c.items() if v>1], key=lambda t:(t[0][1],t[0][0]))
print(f"\nASSIGN dx={dx} dy={dy} phase=({phx:.1f},{phy:.1f})")
print(f"cells total={len(cells)} unique={len(set(cells))} dups={len(cells)-len(set(cells))}")
print("duplicate cells:", dups)
cjs = sorted(set(cj for _,cj in cells))
print("cj rows present:", cjs)
for cj in cjs:
    cis = sorted(ci for ci,c2 in cells if c2==cj)
    print(f"  cj={cj}: ci={cis}")
