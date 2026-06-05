"""Medir espaciado real de agujeros en la nueva imagen."""
import cv2, numpy as np

base = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES'

for path, name in [
    (base + r'\05-06-2026-ESTERILLA_1\frame_0010.png', 'ESTERILLA'),
    (base + r'\05-06-2026-MICROPERFORADO_1\frame_0010.png', 'MICRO'),
]:
    img = cv2.imread(path)
    roi_x = 215 if 'ESTERILLA' in name else 195
    roi_w = 275 if 'ESTERILLA' in name else 295
    roi = img[0:480, roi_x:roi_x+roi_w]

    if 'ESTERILLA' in name:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, -5)
        min_area = 80
    else:
        ch = roi[:, :, 2]
        _, mask = cv2.threshold(ch, 100, 255, cv2.THRESH_BINARY)
        min_area = 15

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    holes = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area: continue
        perim = cv2.arcLength(c, True)
        circ = 4*np.pi*a/perim**2 if perim > 0 else 0
        if circ < 0.15: continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        holes.append((float(cx), float(cy), float(r), float(a)))

    print(f'\n=== {name} (n={len(holes)}) ===')
    # Sort by Y then X to find rows
    holes_s = sorted(holes, key=lambda h: h[1])

    # Find DY: cluster holes into rows
    row_ys = []
    curr_row_y = holes_s[0][1] if holes_s else 0
    row_ys.append(curr_row_y)
    for cx, cy, r, a in holes_s[1:]:
        if cy - curr_row_y > 5:
            curr_row_y = cy
            row_ys.append(cy)
    row_dys = [row_ys[i+1]-row_ys[i] for i in range(len(row_ys)-1) if row_ys[i+1]-row_ys[i] < 50]
    if row_dys:
        print(f'  DY between rows: median={np.median(row_dys):.1f}  values={[f"{d:.1f}" for d in sorted(set(round(d) for d in row_dys))[:6]]}')

    # For each row, find DX
    rows = {}
    for cx, cy, r, a in holes_s:
        key = round(cy / 5) * 5
        rows.setdefault(key, []).append(cx)
    row_dxs = []
    for ry, xs in rows.items():
        xs = sorted(xs)
        for i in range(len(xs)-1):
            d = xs[i+1] - xs[i]
            if 5 < d < 80:
                row_dxs.append(d)
    if row_dxs:
        print(f'  DX within row: median={np.median(row_dxs):.1f}  values={[f"{d:.1f}" for d in sorted(set(round(d) for d in row_dxs))[:6]]}')

    # Radius distribution
    radii = [h[2] for h in holes]
    print(f'  radius: mean={np.mean(radii):.1f}  min={min(radii):.1f}  max={max(radii):.1f}  p25={np.percentile(radii,25):.1f}  p75={np.percentile(radii,75):.1f}')

    # Debug image
    dbg = roi.copy()
    for cx, cy, r, a in holes:
        cv2.circle(dbg, (int(cx), int(cy)), int(r), (0,255,0), 1)
        cv2.circle(dbg, (int(cx), int(cy)), 2, (0,0,255), -1)
    cv2.imwrite(f'data/debug_grid_{name.lower()}.png', dbg)
    print(f'  saved data/debug_grid_{name.lower()}.png')
