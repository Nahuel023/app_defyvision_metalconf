"""Diagnostico de deteccion raw para esterilla."""
import cv2
import numpy as np
from pathlib import Path
from src.utils.config import load_tolerances
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.patterns.roi import load_roi, apply_roi
from src.io.load_images import load_bgr_image

FRAME = "C:/Users/DefyC/Downloads/Patron_Esterilla_METALCONF/frame_0162.png"

img  = load_bgr_image(FRAME)
tols = load_tolerances("modelo_A")
roi  = load_roi("modelo_A", "scanner_2")
img  = apply_roi(img, roi)

print(f"ROI size: {img.shape[1]}x{img.shape[0]}")
print(f"threshold={tols['threshold']} channel={tols.get('use_channel')} polarity={tols.get('polarity')}")
print(f"min_area={tols['min_area']} circ_min={tols['circularity_min']}")

mask = preprocess_for_holes(
    img,
    threshold=int(tols["threshold"]),
    use_channel=tols.get("use_channel", "r"),
    polarity=tols.get("polarity", "bright"),
    use_clahe=bool(tols.get("use_clahe", False)),
    blur_ksize=int(tols.get("blur_ksize", 5)),
    open_ksize=int(tols.get("open_ksize", 3)),
    close_ksize=int(tols.get("close_ksize", 5)),
)

holes = detect_holes_from_mask(
    mask,
    min_area=float(tols["min_area"]),
    max_area=None,
    circularity_min=float(tols["circularity_min"]),
    aspect_ratio_max=float(tols["aspect_ratio_max"]),
    edge_margin_px=float(tols["edge_margin_px"]),
)

areas  = sorted([h.area for h in holes])
radii  = sorted([h.r    for h in holes])
circs  = sorted([h.circularity for h in holes])
small  = [h for h in holes if h.area < 1000]
large  = [h for h in holes if h.area >= 1000]

print(f"\nTotal detectados: {len(holes)}")
print(f"  Chicos (<1000px²): {len(small)}")
print(f"  Grandes (>=1000px²): {len(large)}")
print(f"Areas:  min={areas[0]:.0f}  med={np.median(areas):.0f}  max={areas[-1]:.0f}")
print(f"Radios: min={radii[0]:.1f}  med={np.median(radii):.1f}  max={radii[-1]:.1f}")
print(f"Circ:   min={circs[0]:.2f} med={np.median(circs):.2f} max={circs[-1]:.2f}")

if small:
    sa = sorted([h.area for h in small])
    print(f"  small areas: min={sa[0]:.0f} med={np.median(sa):.0f} max={sa[-1]:.0f}")
if large:
    la = sorted([h.area for h in large])
    print(f"  large areas: min={la[0]:.0f} med={np.median(la):.0f} max={la[-1]:.0f}")

# Guardar mascara con agujeros marcados
vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
for h in small:
    cv2.circle(vis, (int(h.x), int(h.y)), int(h.r), (0, 255, 255), 1)
for h in large:
    cv2.circle(vis, (int(h.x), int(h.y)), int(h.r), (0, 255, 0), 1)
cv2.imwrite("data/debug_esterilla_mask_0162.png", mask)
cv2.imwrite("data/debug_esterilla_detected_0162.png", vis)
print("\nSaved: data/debug_esterilla_mask_0162.png  y  data/debug_esterilla_detected_0162.png")
