"""Save aligned ROI of a frame (raw, no overlay) for inspection."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
from src.io.load_images import load_bgr_image
from src.patterns.roi import load_roi, apply_roi
from src.pipeline.align_edge import align_image_by_right_edge

p = Path(sys.argv[1])
img = load_bgr_image(p)
img_a, _ = align_image_by_right_edge(img)
roi = load_roi("modelo_A", "scanner_2")
crop = apply_roi(img_a, roi) if roi else img_a
crop = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
out = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data\debug_raw_roi.png"
cv2.imwrite(out, crop)
print("saved", out, crop.shape)
