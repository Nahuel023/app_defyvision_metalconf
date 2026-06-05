"""Diagnose WHERE missing holes are in microperforado baseline frames."""
import sys, os, cv2, numpy as np
sys.path.insert(0, '.')
from src.inspection import inspect_image
from pathlib import Path

folder = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-MICROPERFORADO_1'
frames = sorted(f for f in os.listdir(folder) if f.endswith('.png'))

all_missing_x = []
all_missing_y = []
per_frame_missing = []

for fname in frames[:20]:
    res = inspect_image('modelo_B', Path(os.path.join(folder, fname)))
    pts = res.report.missing_points
    per_frame_missing.append(len(pts))
    for px, py in pts:
        all_missing_x.append(px)
        all_missing_y.append(py)

print(f"Frames analyzed: 20  (baseline)")
print(f"Missing per frame: min={min(per_frame_missing)} max={max(per_frame_missing)} avg={np.mean(per_frame_missing):.1f}")

roi_w = 295  # modelo_B ROI width
roi_h = 480
print(f"\nMissing X distribution (ROI 0-{roi_w}px):")
print(f"  x < 40px (left edge):      {sum(1 for x in all_missing_x if x < 40)}")
print(f"  40-120px (center-left):    {sum(1 for x in all_missing_x if 40 <= x < 120)}")
print(f"  120-200px (center):        {sum(1 for x in all_missing_x if 120 <= x < 200)}")
print(f"  x >= 200px (right):        {sum(1 for x in all_missing_x if x >= 200)}")

print(f"\nMost common missing X zones (rounded to 18px columns):")
from collections import Counter
cx = Counter(round(x/18)*18 for x in all_missing_x)
for xzone, cnt in sorted(cx.items(), key=lambda t: -t[1])[:12]:
    print(f"  x~{xzone:3d}px: {cnt:3d} times  ({cnt/20:.1f}/frame)")

print(f"\nMissing Y distribution:")
print(f"  y < 50px (top edge):     {sum(1 for y in all_missing_y if y < 50)}")
print(f"  50-430px (center):       {sum(1 for y in all_missing_y if 50 <= y <= 430)}")
print(f"  y > 430px (bottom edge): {sum(1 for y in all_missing_y if y > 430)}")
