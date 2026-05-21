"""Debug detection on new test images to diagnose false positives and missed holes."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cv2
import numpy as np
from pathlib import Path

from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import apply_roi, load_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.utils.config import load_tolerances

model      = "modelo_B"
scanner_id = "scanner_1"
folder     = Path("C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741")
tol        = load_tolerances(model)
roi        = load_roi(model, scanner_id)
pattern    = load_pattern(find_pattern_path(model, scanner_id))

print(f"Patron: {len(pattern.points)} puntos  dx={pattern.dx:.1f}  dy={pattern.dy:.1f}")
xs = [p[0] for p in pattern.points]
ys = [p[1] for p in pattern.points]
print(f"Patron X: {min(xs):.0f}-{max(xs):.0f}   Y: {min(ys):.0f}-{max(ys):.0f}")
print()

# Analiza 3 frames representativos
for fname in ["frame_0001.png", "frame_0050.png", "frame_0100.png"]:
    img_full = cv2.imread(str(folder / fname))
    img_al, _ = align_image_by_right_edge(img_full)
    img = apply_roi(img_al, roi) if roi else img_al

    mask = preprocess_for_holes(
        img,
        threshold=int(tol["threshold"]),
        use_channel=str(tol["use_channel"]),
        polarity=str(tol["polarity"]),
        use_clahe=bool(tol.get("use_clahe", False)),
        clahe_clip=float(tol.get("clahe_clip", 2.0)),
        clahe_tile=int(tol.get("clahe_tile", 8)),
        use_otsu=bool(tol.get("use_otsu", False)),
    )

    # Deteccion sin filtros para ver todo
    holes_all = detect_holes_from_mask(mask, min_area=50.0, circularity_min=0.4,
                                       aspect_ratio_max=4.0, edge_margin_px=0.0)
    areas = sorted([h.area for h in holes_all])
    radii = [h.r for h in holes_all]

    print(f"=== {fname} ===")
    print(f"  Total contornos detectados (sin filtro): {len(holes_all)}")
    print(f"  Radio: min={min(radii):.1f}  max={max(radii):.1f}  med={np.median(radii):.1f}")
    print(f"  Histograma de areas:")
    bins = [0, 100, 200, 300, 400, 500, 600, 800, 1000, 1500, 9999]
    for i in range(len(bins) - 1):
        count = sum(1 for a in areas if bins[i] <= a < bins[i + 1])
        bar   = "#" * min(count, 60)
        print(f"    {bins[i]:5d}-{bins[i+1]:5d}: {count:3d}  {bar}")
    print()

# Guardar debug visual del primer frame
img_full = cv2.imread(str(folder / "frame_0001.png"))
img_al, _ = align_image_by_right_edge(img_full)
img = apply_roi(img_al, roi) if roi else img_al
mask = preprocess_for_holes(
    img,
    threshold=int(tol["threshold"]),
    use_channel=str(tol["use_channel"]),
    polarity=str(tol["polarity"]),
    use_clahe=bool(tol.get("use_clahe", False)),
    clahe_clip=float(tol.get("clahe_clip", 2.0)),
    clahe_tile=int(tol.get("clahe_tile", 8)),
    use_otsu=bool(tol.get("use_otsu", False)),
)
holes_all = detect_holes_from_mask(mask, min_area=50.0, circularity_min=0.4,
                                   aspect_ratio_max=4.0, edge_margin_px=0.0)

debug = img.copy()
for h in holes_all:
    if h.area >= 300:
        color = (0, 255, 0)      # verde - probablemente real
    elif h.area >= 150:
        color = (0, 165, 255)    # naranja - dudoso
    else:
        color = (0, 0, 255)      # rojo - probablemente ruido
    cv2.circle(debug, (int(h.x), int(h.y)), int(h.r), color, 2)
    cv2.circle(debug, (int(h.x), int(h.y)), 2, color, -1)

cv2.imwrite("data/debug_all_holes.png", debug)
cv2.imwrite("data/debug_mask_frame1.png", mask)
print("Guardado: data/debug_all_holes.png  (verde>=300px2, naranja>=150, rojo<150)")
print("Guardado: data/debug_mask_frame1.png")

# Guardar imagen completa original alineada para referencia
cv2.imwrite("data/debug_frame1_roi.png", img)
print("Guardado: data/debug_frame1_roi.png")
