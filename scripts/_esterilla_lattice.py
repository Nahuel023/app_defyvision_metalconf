"""Fit large/small sublattices of the esterilla to derive true grid geometry."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_A", "scanner_2"
img_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0162.png")
tol = load_tolerances(MODEL)
img_full = load_bgr_image(img_path)
img_aligned, _ = align_image_by_right_edge(img_full)
roi = load_roi(MODEL, SCANNER)
img = apply_roi(img_aligned, roi) if roi is not None else img_aligned
h, w = img.shape[:2]
mask = preprocess_for_holes(
    img, threshold=int(tol["threshold"]), use_channel=str(tol["use_channel"]),
    polarity=str(tol["polarity"]), use_clahe=bool(tol.get("use_clahe", False)),
    clahe_clip=float(tol.get("clahe_clip", 2.0)), clahe_tile=int(tol.get("clahe_tile", 8)),
    use_otsu=bool(tol.get("use_otsu", False)), blur_ksize=int(tol.get("blur_ksize", 5)),
    open_ksize=int(tol.get("open_ksize", 3)), close_ksize=int(tol.get("close_ksize", 5)),
)
margin = float(tol.get("pattern_edge_margin_px", 40.0))
holes = detect_holes_from_mask(
    mask, min_area=float(tol["min_area"]), circularity_min=float(tol["circularity_min"]),
    aspect_ratio_max=float(tol["aspect_ratio_max"]), edge_margin_px=margin,
)
xy = np.array([(hh.x, hh.y) for hh in holes]); r = np.array([hh.r for hh in holes])
split = 20.0
large = xy[r >= split]; small = xy[r < split]
print(f"total={len(xy)} large(r>={split})={len(large)} small={len(small)}")

def median_step(a, ortho_tol):
    """Median nearest-neighbor along axis0 grouping by axis1 within ortho_tol."""
    main, oth = a[:,0], a[:,1]
    ds=[]
    for i in range(len(a)):
        m = np.abs(oth-oth[i])<ortho_tol; m[i]=False
        if m.any():
            dd=np.abs(main[m]-main[i]); dd=dd[dd>5]
            if len(dd): ds.append(dd.min())
    return np.median(ds) if ds else float('nan'), len(ds)

for name, s in [("LARGE", large), ("SMALL", small)]:
    if len(s) < 4:
        print(f"{name}: too few"); continue
    dx_m,_ = median_step(s, 18)                 # same row -> x step
    dy_m,_ = median_step(s[:,::-1], 18)         # same col -> y step (swap axes)
    print(f"{name}: dx={dx_m:.2f} dy={dy_m:.2f}  x[{s[:,0].min():.0f}..{s[:,0].max():.0f}] y[{s[:,1].min():.0f}..{s[:,1].max():.0f}]")

# Offset between large and small sublattices (small sits in interstitial)
if len(large)>4 and len(small)>4:
    # for each small, nearest large; report median signed dx,dy
    d = small[:,None,:]-large[None,:,:]
    dist = (d**2).sum(2)
    j = dist.argmin(1)
    off = small - large[j]
    print(f"small->nearest large offset: dx med={np.median(off[:,0]):.1f} dy med={np.median(off[:,1]):.1f}")
    print(f"  large lattice approx: dx={median_step(large,18)[0]:.1f} dy={median_step(large[:,::-1],18)[0]:.1f}")
