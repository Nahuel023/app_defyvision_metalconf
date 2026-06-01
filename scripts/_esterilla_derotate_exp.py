"""Prototype: de-rotate detected holes by the measured lattice tilt, then run the
grid match, and compare missing before/after. Validates the de-rotation fix."""
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.grid_fitting import grid_compare_points, estimate_lattice_tilt_deg
from src.pipeline.compare import compare_missing_only
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_A", "scanner_2"
FOLDER = Path(r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF")
tol = load_tolerances(MODEL)
roi = load_roi(MODEL, SCANNER)
pat = load_pattern(find_pattern_path(MODEL, SCANNER))
tol_xy = float(tol["tol_xy_px"]); em = float(tol.get("edge_margin_px", 3.0))
stag = float(pat.stagger_x_odd) if pat.stagger_x_odd is not None else 0.0

def detect(img):
    mask = preprocess_for_holes(
        img, threshold=int(tol["threshold"]), use_channel=str(tol["use_channel"]),
        polarity=str(tol["polarity"]), use_clahe=bool(tol.get("use_clahe", False)),
        clahe_clip=float(tol.get("clahe_clip", 2.0)), clahe_tile=int(tol.get("clahe_tile", 8)),
        use_otsu=bool(tol.get("use_otsu", False)), use_adaptive=bool(tol.get("use_adaptive", False)),
        adaptive_block_size=int(tol.get("adaptive_block_size", 61)), adaptive_c=float(tol.get("adaptive_c", -5.0)),
        blur_ksize=int(tol.get("blur_ksize", 5)), open_ksize=int(tol.get("open_ksize", 3)), close_ksize=int(tol.get("close_ksize", 5)))
    holes = detect_holes_from_mask(mask, min_area=float(tol["min_area"]),
        circularity_min=float(tol["circularity_min"]), aspect_ratio_max=float(tol["aspect_ratio_max"]), edge_margin_px=em)
    return np.array([(h.x, h.y) for h in holes], dtype=np.float32)

def match_missing(det_xy, img_w, img_h):
    cp, cc = grid_compare_points(det_xy, pat.cells, pat.dx, pat.dy, pat.phase_x, pat.phase_y,
        None, img_w, img_h, em, tol_affine=tol_xy*1.5, stagger_x_odd=stag)
    rep = compare_missing_only(cp, [tuple(p) for p in det_xy], tol_xy_px=tol_xy,
        max_missing=int(tol.get("grid_max_missing", 50)), extra_min_dist_factor=float(tol.get("extra_min_dist_factor", 0.0)))
    return rep.missing, len(cp)

def rotate(xy, deg, cx, cy):
    t = math.radians(deg); c, s = math.cos(t), math.sin(t)
    x = xy[:, 0] - cx; y = xy[:, 1] - cy
    return np.stack([c*x - s*y + cx, s*x + c*y + cy], axis=1).astype(np.float32)

for name in ("frame_0016","frame_0074","frame_0090","frame_0097","frame_0120","frame_0162","frame_0100"):
    p = FOLDER / f"{name}.png"
    img_full = load_bgr_image(p); img_a,_ = align_image_by_right_edge(img_full)
    img = apply_roi(img_a, roi) if roi else img_a
    h, w = img.shape[:2]
    det = detect(img)
    tilt = estimate_lattice_tilt_deg(det, float(pat.dx))
    m0, n0 = match_missing(det, w, h)
    cx, cy = w/2.0, h/2.0
    det_d = rotate(det, -tilt, cx, cy)
    m1, n1 = match_missing(det_d, w, h)
    print(f"{name}: tilt={tilt:+.2f}  missing SIN={m0:3d}  missing DEROT={m1:3d}  (det={len(det)})")
