"""Analyze the real geometry of Esterilla holes in the new frames."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import cv2
import numpy as np
from pathlib import Path
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.grid_fitting import estimate_spacing

FOLDER = Path(r'C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF')
frames = sorted(FOLDER.glob('*.png'))

def detect(img):
    mask = preprocess_for_holes(
        img, threshold=0, use_channel='r', polarity='bright',
        use_clahe=True, clahe_clip=3.0, clahe_tile=8,
        use_otsu=True, blur_ksize=5, open_ksize=3, close_ksize=5,
    )
    return detect_holes_from_mask(
        mask, min_area=100, circularity_min=0.45, aspect_ratio_max=3.0, edge_margin_px=3,
    )

# Scan all frames for hole count
print("=== All frames hole count ===")
counts = []
for i, f in enumerate(frames):
    img = cv2.imread(str(f))
    h = detect(img)
    counts.append(len(h))
    if i < 10 or i % 20 == 0:
        print(f"  frame_{i:04d}: {len(h)} holes")

print(f"\nMin holes: {min(counts)} (frame {counts.index(min(counts))})")
print(f"Max holes: {max(counts)} (frame {counts.index(max(counts))})")
print(f"Median:    {sorted(counts)[len(counts)//2]}")

# Deep analysis on best frame
best_idx = counts.index(max(counts))
print(f"\n=== Deep analysis: frame_{best_idx:04d} ({max(counts)} holes) ===")
img = cv2.imread(str(frames[best_idx]))
holes = detect(img)

xs = np.array([h.x for h in holes])
ys = np.array([h.y for h in holes])
areas = np.array([h.area for h in holes])

xs_sorted = np.sort(xs)
gaps_x = np.diff(xs_sorted)
col_breaks = np.where(gaps_x > 25)[0]
col_centers = []
prev = 0
for b in col_breaks:
    col_centers.append(float(np.mean(xs_sorted[prev:b+1])))
    prev = b+1
col_centers.append(float(np.mean(xs_sorted[prev:])))
print(f"Column centers: {[f'{x:.1f}' for x in col_centers]}")
print(f"Col spacings:   {[f'{col_centers[i+1]-col_centers[i]:.1f}' for i in range(len(col_centers)-1)]}")

dx = estimate_spacing(xs, min_spacing=20.0)
dy = estimate_spacing(ys, min_spacing=18.0)
print(f"Grid dx={dx:.1f} dy={dy:.1f}")

split = 1000
small = areas[areas < split]
large = areas[areas >= split]
print(f"Small (<{split}): {len(small)}, mean={small.mean():.0f}, range={small.min():.0f}-{small.max():.0f}")
print(f"Large (>={split}): {len(large)}, mean={large.mean():.0f}, range={large.min():.0f}-{large.max():.0f}")

# Show first 30 holes sorted by y
holes_s = sorted(holes, key=lambda h: h.y)
print("\n y      x      area  type")
for h in holes_s[:30]:
    t = 'S' if h.area < split else 'L'
    print(f"{h.y:6.1f}  {h.x:6.1f}  {h.area:5.0f}  {t}")

print(f"\nX extent: {xs.min():.0f} - {xs.max():.0f}")
print(f"Y extent: {ys.min():.0f} - {ys.max():.0f}")

# Save annotated debug image
dbg = img.copy()
for h in holes:
    col = (0, 255, 0) if h.area < split else (0, 165, 255)
    cv2.circle(dbg, (int(h.x), int(h.y)), int(h.r), col, 2)
cv2.imwrite('data/debug_esterilla_best.png', dbg)
print(f"\nSaved: data/debug_esterilla_best.png (green=small, orange=large)")
