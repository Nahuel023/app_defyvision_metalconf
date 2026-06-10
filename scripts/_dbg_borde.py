"""Diagnostic: compare pattern boundary detection before/after pct10 fix."""
import numpy as np, sys, cv2
sys.path.insert(0, ".")
from src.utils.config import load_tolerances
from src.patterns.pattern_io import load_pattern
from src.patterns.roi import load_roi
from src.pipeline.edge_centering import _N_BANDS, _iqr_filter_band_dict, _split_holes_into_y_rows
from src.pipeline.detect_holes import Hole
from pathlib import Path

pat  = load_pattern(Path("data/patterns/modelo_B/holes.json"))
roi  = load_roi("modelo_B")
tols = load_tolerances("modelo_B")
n_bands = int(tols.get("edge_centering_bands", _N_BANDS))
img_h   = int(roi.h) if roi else 480
holes = [Hole(x=p[0], y=p[1], r=pat.radii[i], area=100.0, circularity=0.9)
         for i, p in enumerate(pat.points)]

xs_all = np.array([hh.x for hh in holes])
old_left, old_right = float(xs_all.min()), float(xs_all.max())
new_left, new_right = float(np.percentile(xs_all, 10)), float(np.percentile(xs_all, 90))
print(f"ANTES  ref: left={old_left:.1f} right={old_right:.1f}")
print(f"DESPUES ref: left={new_left:.1f} right={new_right:.1f}")

btol = 8.0
all_holes = list(holes)
band_h = img_h / n_bands
row_tol_px = max(3.0, min(band_h * 0.45, float(np.median([hh.r for hh in all_holes])) * 1.6))

def compute_bounds(ref_left, ref_right):
    ld, rd = {}, {}
    for i in range(n_bands):
        y0, y1 = i * band_h, (i + 1) * band_h
        band_mid = (y0 + y1) / 2.0
        bh = [hh for hh in all_holes if y0 <= hh.y < y1]
        if not bh:
            continue
        lc, rc = [], []
        for rh in _split_holes_into_y_rows(bh, row_tol_px):
            lholes = [hh for hh in rh if hh.x <= ref_left + btol]
            rholes = [hh for hh in rh if hh.x >= ref_right - btol]
            if lholes:
                cy = float(np.median([hh.y for hh in lholes]))
                lc.append((abs(cy - band_mid), float(min(hh.x for hh in lholes)), cy))
            if rholes:
                cy = float(np.median([hh.y for hh in rholes]))
                rc.append((abs(cy - band_mid), float(max(hh.x for hh in rholes)), cy))
        if lc:
            best = min(lc, key=lambda item: item[0])
            ld[i] = (best[1], best[2])
        if rc:
            best = min(rc, key=lambda item: item[0])
            rd[i] = (best[1], best[2])
    return _iqr_filter_band_dict(ld), _iqr_filter_band_dict(rd)

ld_old, rd_old = compute_bounds(old_left, old_right)
ld_new, rd_new = compute_bounds(new_left, new_right)

img = cv2.imread(r"C:\Users\DefyC\Downloads\05-06-2026-PATRONES EDITADOS\05-06-2026-MICROPERFORADO_1\frame_0020.png")
ri = img[int(roi.y):int(roi.y + roi.h), int(roi.x):int(roi.x + roi.w)]

def draw_b(im, ld, rd, cl, cr, label):
    out = im.copy()
    def polyline(d, c):
        pts = sorted(d.values(), key=lambda v: v[1])
        for j in range(len(pts) - 1):
            cv2.line(out, (int(pts[j][0]), int(pts[j][1])),
                     (int(pts[j + 1][0]), int(pts[j + 1][1])), c, 3)
        for v in pts:
            cv2.circle(out, (int(v[0]), int(v[1])), 5, c, -1)
    polyline(ld, cl)
    polyline(rd, cr)
    cv2.putText(out, label, (5, 25), cv2.FONT_HERSHEY_PLAIN, 1.4, (255, 255, 255), 2)
    return out

vis_old = draw_b(ri, ld_old, rd_old, (0, 0, 255), (0, 0, 255), f"ANTES: izq={len(ld_old)} bands")
vis_new = draw_b(ri, ld_new, rd_new, (0, 255, 0), (0, 255, 0), f"DESPUES: izq={len(ld_new)} bands")
combined = np.hstack([vis_old, vis_new])
combined = cv2.resize(combined, (combined.shape[1] * 2, combined.shape[0] * 2),
                      interpolation=cv2.INTER_NEAREST)
cv2.imwrite("data/dbg_borde_antes_despues.png", combined)
print(f"ANTES:   left={len(ld_old)} right={len(rd_old)} bands")
print(f"DESPUES: left={len(ld_new)} right={len(rd_new)} bands")
print("Saved: data/dbg_borde_antes_despues.png")
