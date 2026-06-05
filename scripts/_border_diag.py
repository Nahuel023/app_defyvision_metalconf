"""Diagnose CHAPA and PATRON border line consistency across frames."""
import sys, os, cv2, numpy as np, json
sys.path.insert(0, '.')
from src.inspection import inspect_image
from pathlib import Path

folder = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-ESTERILLA_1'
with open('data/patterns/modelo_A/roi.json') as f:
    roi_d = json.load(f)
rx, ry, rw, rh = roi_d['x'], roi_d['y'], roi_d['w'], roi_d['h']

frames = sorted(f for f in os.listdir(folder) if f.endswith('.png'))

# Collect per-frame centering metrics for sharp frames
chapa_stds, chapa_maxs = [], []
pat_stds, pat_maxs = [], []
left_xs, right_xs = [], []  # scalar positions across frames

for fname in frames[:60]:
    img = cv2.imread(os.path.join(folder, fname))
    gray = cv2.cvtColor(img[ry:ry+rh, rx:rx+rw], cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if blur < 650:
        continue
    res = inspect_image('modelo_A', Path(os.path.join(folder, fname)))
    if res.centering is None:
        continue
    c = res.centering
    chapa_stds.append(c.chapa_zigzag_std_px)
    chapa_maxs.append(c.chapa_zigzag_max_px)
    pat_stds.append(c.pattern_zigzag_std_px)
    pat_maxs.append(c.pattern_zigzag_max_px)
    left_xs.append(c.left_x)
    right_xs.append(c.right_x)

n = len(chapa_stds)
print(f"Sharp frames analyzed: {n}")
if n:
    print(f"\nCHAPA zigzag (within-frame band-to-band consistency):")
    print(f"  std_px  avg={np.mean(chapa_stds):.2f}  max={max(chapa_stds):.2f}")
    print(f"  max_px  avg={np.mean(chapa_maxs):.2f}  max={max(chapa_maxs):.2f}")
    print(f"\nPATRON zigzag (within-frame):")
    print(f"  std_px  avg={np.mean(pat_stds):.2f}  max={max(pat_stds):.2f}")
    print(f"  max_px  avg={np.mean(pat_maxs):.2f}  max={max(pat_maxs):.2f}")
    print(f"\nCHAPA scalar edge position (across frames — sheet lateral drift):")
    print(f"  left_x   avg={np.mean(left_xs):.1f}  std={np.std(left_xs):.1f}  range={min(left_xs):.1f}-{max(left_xs):.1f}")
    print(f"  right_x  avg={np.mean(right_xs):.1f}  std={np.std(right_xs):.1f}  range={min(right_xs):.1f}-{max(right_xs):.1f}")
