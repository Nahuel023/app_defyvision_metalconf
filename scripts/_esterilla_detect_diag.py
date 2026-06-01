"""Diagnose which real holes the detector misses and why (per-filter)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2, numpy as np

from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

MODEL, SCANNER = "modelo_A", "scanner_2"
OUT = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data"
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
cv2.imwrite(OUT + r"\debug_det_mask.png", mask)

em = float(tol.get("edge_margin_px", 3.0))   # INSPECTION margin (not build)
cur = detect_holes_from_mask(
    mask, min_area=float(tol["min_area"]), circularity_min=float(tol["circularity_min"]),
    aspect_ratio_max=float(tol["aspect_ratio_max"]), edge_margin_px=em,
)
relaxed = detect_holes_from_mask(
    mask, min_area=15.0, circularity_min=0.05, aspect_ratio_max=100.0, edge_margin_px=em,
)
print(f"current filters: min_area={tol['min_area']} circ_min={tol['circularity_min']} aspect_max={tol['aspect_ratio_max']} edge_margin={em}")
print(f"detected current={len(cur)}  relaxed={len(relaxed)}")

cur_xy = {(round(hh.x), round(hh.y)) for hh in cur}
missed = []
for hh in relaxed:
    # match to a current hole within 6px
    if any(abs(hh.x-cx)<6 and abs(hh.y-cy)<6 for cx,cy in cur_xy):
        continue
    missed.append(hh)
print(f"\nHoles present in mask but REJECTED by current filters: {len(missed)}")
print(f"{'x':>6}{'y':>6}{'area':>8}{'circ':>7}{'r':>6}   reason")
for hh in sorted(missed, key=lambda z:(z.y,z.x)):
    reasons = []
    if hh.area < float(tol["min_area"]): reasons.append(f"area<{tol['min_area']}")
    if hh.circularity < float(tol["circularity_min"]): reasons.append(f"circ<{tol['circularity_min']}")
    reasons = reasons or ["aspect/edge/other"]
    print(f"{hh.x:>6.0f}{hh.y:>6.0f}{hh.area:>8.0f}{hh.circularity:>7.2f}{hh.r:>6.1f}   {','.join(reasons)}")

# render overlay: green=current, red=missed
ov = img.copy()
for hh in cur:
    cv2.circle(ov, (int(hh.x),int(hh.y)), int(hh.r), (0,255,0), 2)
for hh in missed:
    cv2.circle(ov, (int(hh.x),int(hh.y)), max(int(hh.r),6), (0,0,255), 2)
cv2.imwrite(OUT + r"\debug_det_overlay.png", ov)
top = ov[0:540,:]; cv2.imwrite(OUT + r"\debug_det_top.png", cv2.resize(top,(top.shape[1]*2,top.shape[0]*2),interpolation=cv2.INTER_NEAREST))
bot = ov[540:1080,:]; cv2.imwrite(OUT + r"\debug_det_bot.png", cv2.resize(bot,(bot.shape[1]*2,bot.shape[0]*2),interpolation=cv2.INTER_NEAREST))
print("saved debug_det_mask/overlay/top/bot")
