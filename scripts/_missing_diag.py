"""Diagnose WHERE missing holes are in clear esterilla frames."""
import sys, os, cv2, numpy as np, json
sys.path.insert(0, '.')
from src.inspection import inspect_image
from src.patterns.pattern_io import load_pattern
from src.utils.config import load_tolerances
from pathlib import Path

folder = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-ESTERILLA_1'
with open('data/patterns/modelo_A/roi.json') as f:
    roi_d = json.load(f)
rx, ry, rw, rh = roi_d['x'], roi_d['y'], roi_d['w'], roi_d['h']

# Compute blur for all frames
frames = sorted(f for f in os.listdir(folder) if f.endswith('.png'))
sharp_frames = []
for fname in frames:
    img = cv2.imread(os.path.join(folder, fname))
    gray = cv2.cvtColor(img[ry:ry+rh, rx:rx+rw], cv2.COLOR_BGR2GRAY)
    score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if score >= 800:  # only very sharp frames
        sharp_frames.append((fname, score))

print(f"Very sharp frames (>=800): {len(sharp_frames)}")

# For each sharp frame, run inspection and collect missing positions
all_missing_x = []
all_missing_y = []
for fname, score in sharp_frames[:20]:  # analyze up to 20
    res = inspect_image('modelo_A', Path(os.path.join(folder, fname)))
    for px, py in res.report.missing_points:
        all_missing_x.append(px)
        all_missing_y.append(py)

print(f"\nTotal missing positions across {min(20, len(sharp_frames))} frames: {len(all_missing_x)}")
if all_missing_x:
    print(f"Missing X distribution (ROI-relative):")
    print(f"  x < 60px (left edge):  {sum(1 for x in all_missing_x if x < 60)}")
    print(f"  60-200px (center):     {sum(1 for x in all_missing_x if 60 <= x <= 200)}")
    print(f"  x > 200px (right edge):{sum(1 for x in all_missing_x if x > 200)}")
    print(f"\nMissing Y distribution:")
    print(f"  y < 60px (top):      {sum(1 for y in all_missing_y if y < 60)}")
    print(f"  60-420px (center):   {sum(1 for y in all_missing_y if 60 <= y <= 420)}")
    print(f"  y > 420px (bottom):  {sum(1 for y in all_missing_y if y > 420)}")

    print(f"\nMost common missing X positions (top 10):")
    from collections import Counter
    cx = Counter(round(x/20)*20 for x in all_missing_x)
    for xzone, cnt in sorted(cx.items(), key=lambda t: -t[1])[:10]:
        print(f"  x~{xzone}px: {cnt} times")
