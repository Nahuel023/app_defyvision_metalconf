"""Debug exact missing hole positions for modelo_B."""
import json
import numpy as np
import cv2
import sys
sys.path.insert(0, '.')

from src.pipeline.grid_fitting import grid_compare_points, _fit_affine_to_grid
from src.patterns.pattern_io import load_pattern
from pathlib import Path

pat = load_pattern(Path('data/patterns/modelo_B/holes.json'))
print(f"Pattern: {len(pat.points)} pts, dx={pat.dx}, dy={pat.dy}")
print(f"phase_x={pat.phase_x:.2f}, phase_y={pat.phase_y:.2f}")
print(f"stagger={pat.stagger_x_odd}, cells={len(pat.cells)}")

# Detect with pipeline settings (blur=1, thr=120, r channel)
img = cv2.imread(r'C:\Users\DefyC\Downloads\Sony_IP_Camera_Imagenes\MICROPERFORADO_2\frame_0005.png')
rx, ry, rw, rh = 230, 0, 185, 480
roi_img = img[ry:ry+rh, rx:rx+rw]
r_ch = roi_img[:, :, 2]
# pipeline: blur_ksize=1 → no blur, threshold=120, open=1(nop), close=1(nop)
_, mask = cv2.threshold(r_ch, 120, 255, cv2.THRESH_BINARY)
cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
detected = []
for c in cnts:
    a = cv2.contourArea(c)
    if a < 15:
        continue
    perim = cv2.arcLength(c, True)
    circ = 4 * np.pi * a / perim ** 2 if perim > 0 else 0
    if circ < 0.2:
        continue
    M = cv2.moments(c)
    if M["m00"] < 1.0:
        continue
    cx = M["m10"] / M["m00"]
    cy = M["m01"] / M["m00"]
    detected.append([float(cx), float(cy)])

detected_arr = np.array(detected, dtype=np.float32)
print(f"\nDetected (pipeline settings): {len(detected_arr)}")

stagger = float(pat.stagger_x_odd) if pat.stagger_x_odd is not None else 0.0
tol_xy = 16.0
tol_affine = tol_xy * 1.5

compare_pts, compare_cells = grid_compare_points(
    detected_arr,
    list(pat.cells),
    pat.dx, pat.dy,
    pat.phase_x, pat.phase_y,
    None,
    rw, rh,
    0.0,
    tol_affine=tol_affine,
    stagger_x_odd=stagger,
)
print(f"Expected from grid_compare: {len(compare_pts)}")

# Match expected vs detected
missing_pts = []
for (px, py), cell in zip(compare_pts, compare_cells):
    dists = [np.hypot(px - dx, py - dy) for dx, dy in detected]
    best = min(dists) if dists else 999
    if best > tol_xy:
        missing_pts.append((px, py, cell, best))

print(f"Missing (tol={tol_xy}): {len(missing_pts)}")

# Distribution by zone
print("\nMissing by Y zone:")
for zone_y in range(0, 480, 40):
    zm = [(px, py, c, d) for px, py, c, d in missing_pts if zone_y <= py < zone_y + 40]
    if zm:
        print(f"  Y={zone_y}-{zone_y+40}: {len(zm)}  X range: {min(p[0] for p in zm):.0f}-{max(p[0] for p in zm):.0f}  dist: {[f'{p[3]:.1f}' for p in zm[:5]]}")

print("\nMissing by X zone:")
for zone_x in range(0, 185, 20):
    zm = [(px, py, c, d) for px, py, c, d in missing_pts if zone_x <= px < zone_x + 20]
    if zm:
        print(f"  X={zone_x}-{zone_x+20}: {len(zm)}")

# Draw debug overlay showing expected vs detected
dbg = roi_img.copy()
for dx2, dy2 in detected:
    cv2.circle(dbg, (int(dx2), int(dy2)), 4, (0, 255, 0), 1)
for px, py, cell, dist in missing_pts:
    cv2.drawMarker(dbg, (int(px), int(py)), (0, 0, 255), cv2.MARKER_CROSS, 12, 2)
    cv2.putText(dbg, f"{dist:.0f}", (int(px) + 4, int(py) - 4),
                cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 200, 255), 1)
cv2.imwrite('data/debug_micro_missing2.png', dbg)
print("\nDebug: data/debug_micro_missing2.png")
