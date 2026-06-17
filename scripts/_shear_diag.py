"""Measure row-angle vs column-angle (shear) of the hole lattice per frame.

row_angle  = median angle of nearest RIGHT neighbor (deg from horizontal)
col_angle  = median angle of nearest DOWN neighbor  (deg from vertical)
shear      = col_angle - row_angle  (≈0 for pure rotation; large for shear/desalign)
"""
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

tol = load_tolerances("modelo_A")
roi = load_roi("modelo_A", "scanner_2")
dx = float(tol["grid_dx"]); dy = float(tol["grid_dy"])

def holes_xy(p):
    img = load_bgr_image(Path(p)); img_a,_ = align_image_by_right_edge(img)
    im = apply_roi(img_a, roi) if roi else img_a
    mask = preprocess_for_holes(im, threshold=int(tol["threshold"]), use_channel="r", polarity="bright",
        use_clahe=True, clahe_clip=3.0, clahe_tile=8, use_otsu=bool(tol.get("use_otsu",False)),
        use_adaptive=True, adaptive_block_size=61, adaptive_c=-5.0, blur_ksize=5, open_ksize=3, close_ksize=5)
    hs = detect_holes_from_mask(mask, min_area=float(tol["min_area"]), circularity_min=float(tol["circularity_min"]),
        aspect_ratio_max=float(tol["aspect_ratio_max"]), edge_margin_px=3.0)
    return np.array([(h.x,h.y) for h in hs], np.float32)

def angles(xy):
    xs, ys = xy[:,0], xy[:,1]
    row, col = [], []
    for i in range(len(xy)):
        ddx, ddy = xs-xs[i], ys-ys[i]
        # right neighbor in same row
        m = (ddx>0.5*dx)&(ddx<1.4*dx)&(np.abs(ddy)<22)
        if m.any():
            j = np.where(m)[0][np.argmin(ddx[m])]
            row.append(math.degrees(math.atan2(ddy[j], ddx[j])))
        # down neighbor in same column (angle from vertical)
        m2 = (ddy>0.5*dy)&(ddy<1.5*dy)&(np.abs(ddx)<22)
        if m2.any():
            j = np.where(m2)[0][np.argmin(ddy[m2])]
            col.append(math.degrees(math.atan2(ddx[j], ddy[j])))  # x-dev per y → from vertical
    r = float(np.median(row)) if row else float('nan')
    c = float(np.median(col)) if col else float('nan')
    return r, c, len(row), len(col)

for label, p in [
    ("0027 editado (desalin, SI parar)", r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF_editado\frame_0027.png"),
    ("0090 normal (chapa inclinada, NO)", r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0090.png"),
    ("0083 normal (chapa inclinada, NO)", r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0083.png"),
    ("0162 normal (OK)",                 r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0162.png"),
    ("0078 normal (2 miss, OK)",         r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0078.png"),
]:
    r, c, nr, nc = angles(holes_xy(p))
    print(f"{label:38s} row={r:+.2f}  col={c:+.2f}  shear(col-row)={c-r:+.2f}  (n={nr}/{nc})")
