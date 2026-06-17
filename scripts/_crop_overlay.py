"""Crop a region of a saved overlay (full-frame) around a ROI-relative point."""
import sys
from pathlib import Path
import cv2
# args: overlay_path  roi_x_center roi_y_center  (ROI starts at full-frame x=870)
ov = cv2.imread(sys.argv[1])
rx, ry = int(sys.argv[2]), int(sys.argv[3])
fx, fy = 870 + rx, ry
x0, x1 = max(0, fx - 160), min(ov.shape[1], fx + 160)
y0, y1 = max(0, fy - 110), min(ov.shape[0], fy + 110)
crop = ov[y0:y1, x0:x1]
crop = cv2.resize(crop, (crop.shape[1] * 3, crop.shape[0] * 3), interpolation=cv2.INTER_NEAREST)
out = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data\debug_crop_missing.png"
cv2.imwrite(out, crop)
print("saved", out)
