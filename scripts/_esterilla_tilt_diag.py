"""Measure sheet/lattice tilt per frame and correlate with missing.

For each unique frame:
  - angle_deg: rotation detected by Hough (align_image_by_right_edge)
  - missing: from full inspection
  - lattice_tilt: rotation of the hole lattice estimated from detected holes
                  (median angle of nearest in-row neighbor vectors)
"""
import sys, hashlib, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from src.inspection import inspect_image
from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_A", "scanner_2"
FOLDER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO")
tol = load_tolerances(MODEL)
roi = load_roi(MODEL, SCANNER)

def lattice_tilt_deg(img):
    """Estimate lattice rotation from holes: median angle of nearest +x neighbor."""
    mask = preprocess_for_holes(
        img, threshold=int(tol["threshold"]), use_channel=str(tol["use_channel"]),
        polarity=str(tol["polarity"]), use_clahe=bool(tol.get("use_clahe", False)),
        clahe_clip=float(tol.get("clahe_clip", 2.0)), clahe_tile=int(tol.get("clahe_tile", 8)),
        use_otsu=bool(tol.get("use_otsu", False)),
        use_adaptive=bool(tol.get("use_adaptive", False)),
        adaptive_block_size=int(tol.get("adaptive_block_size", 61)),
        adaptive_c=float(tol.get("adaptive_c", -5.0)),
        blur_ksize=int(tol.get("blur_ksize", 5)), open_ksize=int(tol.get("open_ksize", 3)),
        close_ksize=int(tol.get("close_ksize", 5)),
    )
    holes = detect_holes_from_mask(mask, min_area=float(tol["min_area"]),
        circularity_min=float(tol["circularity_min"]), aspect_ratio_max=float(tol["aspect_ratio_max"]),
        edge_margin_px=float(tol.get("edge_margin_px", 3.0)))
    xy = np.array([(h.x, h.y) for h in holes])
    if len(xy) < 8:
        return float("nan")
    angs = []
    for i in range(len(xy)):
        d = xy - xy[i]
        # candidates roughly to the right within one row (dx~65, |dy|<20)
        m = (d[:, 0] > 30) & (d[:, 0] < 90) & (np.abs(d[:, 1]) < 20)
        if m.any():
            j = np.where(m)[0][np.argmin(d[m, 0])]
            angs.append(math.degrees(math.atan2(d[j, 1], d[j, 0])))
    return float(np.median(angs)) if angs else float("nan")

files = sorted(p for p in FOLDER.iterdir() if p.suffix.lower() == ".png")
seen, uniq = set(), []
for p in files:
    h = hashlib.md5(p.read_bytes()).hexdigest()
    if h not in seen:
        seen.add(h); uniq.append(p)

print(f"{'frame':<16}{'hough°':>8}{'lattice°':>9}{'missing':>8}{'extra':>7}  status")
rows = []
for p in uniq:
    img_full = load_bgr_image(p)
    img_a, ar = align_image_by_right_edge(img_full)
    img = apply_roi(img_a, roi) if roi is not None else img_a
    tilt = lattice_tilt_deg(img)
    r = inspect_image(MODEL, p, scanner_id=SCANNER)
    rows.append((p.stem, ar.angle_deg, tilt, r.report.missing, r.report.extra, r.status))
    print(f"{p.stem:<16}{ar.angle_deg:>8.2f}{tilt:>9.2f}{r.report.missing:>8}{r.report.extra:>7}  {r.status}")

tilts = np.array([abs(r[2]) for r in rows if not math.isnan(r[2])])
miss  = np.array([r[3] for r in rows])
print(f"\nlattice |tilt|: med={np.median(tilts):.2f} max={tilts.max():.2f}")
print(f"corr(|lattice_tilt|, missing) = {np.corrcoef([abs(r[2]) for r in rows],[r[3] for r in rows])[0,1]:.2f}")
