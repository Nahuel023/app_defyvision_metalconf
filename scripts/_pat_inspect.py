"""Inspect pattern structure and test detection vs pattern."""
import json, numpy as np, cv2, sys

model = sys.argv[1] if len(sys.argv) > 1 else 'modelo_A'
with open(f'data/patterns/{model}/holes.json') as f:
    pat = json.load(f)
with open(f'data/patterns/{model}/roi.json') as f:
    roi = json.load(f)

pts = [(p['x'], p['y']) for p in pat['points']]
g = pat['grid']
print(f"Pattern: {len(pts)} pts")
print(f"Grid: dx={g['dx']} dy={g['dy']} phase_x={g['phase_x']} phase_y={g['phase_y']}")
stagger = g.get('stagger_x_odd', 0)
print(f"stagger={stagger}")
print(f"X: {min(p[0] for p in pts):.0f}-{max(p[0] for p in pts):.0f}  Y: {min(p[1] for p in pts):.0f}-{max(p[1] for p in pts):.0f}")

# Check density of Y values to understand row structure
ys = sorted(p[1] for p in pts)
dys = [ys[i+1]-ys[i] for i in range(len(ys)-1) if ys[i+1]-ys[i] > 2]
if dys:
    print(f"DY between pattern pts: min={min(dys):.1f} median={np.median(dys):.1f} max={max(dys):.1f}")

# Now detect holes in the same image used for build
img_path = 'data/debug_grid_esterilla.png' if model == 'modelo_A' else 'data/debug_grid_micro.png'
img_pat = f'data/patterns/{model}/holes.json'
print(f"\nRe-detecting in ROI with same settings as pipeline...")
base = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES'
folder = '05-06-2026-ESTERILLA_1' if model == 'modelo_A' else '05-06-2026-MICROPERFORADO_1'
img = cv2.imread(fr'{base}\{folder}\frame_0010.png')
rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
roi_img = img[ry:ry+rh, rx:rx+rw]

if model == 'modelo_A':
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, -5)
    min_area = 80
else:
    ch = roi_img[:, :, 2]
    _, mask = cv2.threshold(ch, 100, 255, cv2.THRESH_BINARY)
    min_area = 15

cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
detected = []
for c in cnts:
    a = cv2.contourArea(c)
    if a < min_area: continue
    perim = cv2.arcLength(c, True)
    circ = 4*np.pi*a/perim**2 if perim > 0 else 0
    if circ < 0.15: continue
    M = cv2.moments(c)
    if M['m00'] < 1: continue
    cx, cy = M['m10']/M['m00'], M['m01']/M['m00']
    detected.append((float(cx), float(cy)))

print(f"Detected: {len(detected)}")
print(f"Det X: {min(d[0] for d in detected):.0f}-{max(d[0] for d in detected):.0f}")
print(f"Det Y: {min(d[1] for d in detected):.0f}-{max(d[1] for d in detected):.0f}")

# Match each pattern point to nearest detected
unmatched = []
tol = 20
for px, py in pts:
    dists = [np.hypot(px-dx, py-dy) for dx,dy in detected]
    best = min(dists)
    if best > tol:
        unmatched.append((px, py, best))

print(f"\nPattern points not matched within tol={tol}px: {len(unmatched)}/{len(pts)}")
print("First 10 unmatched (x, y, nearest_dist):")
for px, py, d in sorted(unmatched, key=lambda u: u[1])[:10]:
    print(f"  ({px:.1f},{py:.1f}) dist={d:.1f}")
