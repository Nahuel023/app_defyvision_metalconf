"""Diagnóstico de distancias: missing → detectado más cercano, para un frame dado."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from pathlib import Path
from src.inspection import _inspect_bgr
from src.io.load_images import load_bgr_image
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_B", "scanner_1"
FRAME = Path("C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741/frame_0036.png")

tol     = load_tolerances(MODEL)
tol_xy  = float(tol["tol_xy_px"])
img     = load_bgr_image(FRAME)
r       = _inspect_bgr(MODEL, img, image_path=FRAME, scanner_id=SCANNER)
rep     = r.report

print(f"expected={rep.expected}  detected={rep.detected}  missing={rep.missing}  extra={rep.extra}")
print(f"status={r.status}")

if rep.missing_points and r.holes:
    det   = np.array([(h.x, h.y) for h in r.holes], dtype=np.float32)
    mis   = np.array(rep.missing_points, dtype=np.float32)
    diffs = mis[:, None, :] - det[None, :, :]
    dists = np.sqrt((diffs ** 2).sum(axis=2)).min(axis=1)

    print(f"\nDistancias missing -> detectado mas cercano:")
    print(f"  min={dists.min():.1f}  max={dists.max():.1f}  median={np.median(dists):.1f}  mean={dists.mean():.1f}")
    print(f"  < tol ({tol_xy}px):        {int((dists < tol_xy).sum())}  (no deberian existir)")
    print(f"  tol..2xtol ({tol_xy:.0f}-{tol_xy*2:.0f}px): {int(((dists >= tol_xy) & (dists < tol_xy*2)).sum())}  (falsos missing recuperables)")
    print(f"  > 2xtol ({tol_xy*2:.0f}px):     {int((dists >= tol_xy*2).sum())}  (verdaderos missing o ruido)")

    bins = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 80, 200]
    print("\n  Histograma de distancias:")
    for i in range(len(bins) - 1):
        c = int(((dists >= bins[i]) & (dists < bins[i+1])).sum())
        if c:
            print(f"    {bins[i]:4d}-{bins[i+1]:4d}px: {c}  {'#' * c}")

    # Posiciones de los missing y sus detectados mas cercanos
    print("\n  Posiciones (solo los que tienen detectado a 22-60px):")
    near_idx = np.argsort(dists)
    for i in near_idx:
        d = dists[i]
        if tol_xy <= d <= 60:
            ex, ey = rep.missing_points[i]
            j = int(np.argmin(np.sqrt(((mis[i] - det)**2).sum(axis=1))))
            dx, dy = det[j]
            print(f"    exp=({ex:.0f},{ey:.0f})  det=({dx:.0f},{dy:.0f})  dist={d:.1f}px")
else:
    print("Sin missing o sin detectados.")
