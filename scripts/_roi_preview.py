"""Preview proposed ROI boundaries for both patterns."""
import cv2, numpy as np, json

base = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES'

configs = [
    (base + r'\05-06-2026-ESTERILLA_1\frame_0010.png',
     {'x': 215, 'y': 0, 'w': 275, 'h': 480}, 42, 42, 'esterilla'),
    (base + r'\05-06-2026-MICROPERFORADO_1\frame_0010.png',
     {'x': 195, 'y': 0, 'w': 295, 'h': 480}, 42, 42, 'micro'),
]

for path, roi, top_ig, bot_ig, name in configs:
    img = cv2.imread(path)
    rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
    # ROI box
    cv2.rectangle(img, (rx, ry), (rx+rw, ry+rh), (0, 0, 255), 2)
    # Top/bottom ignore lines (inside ROI)
    cv2.line(img, (rx, top_ig), (rx+rw, top_ig), (0, 220, 255), 2)
    cv2.line(img, (rx, rh-bot_ig), (rx+rw, rh-bot_ig), (0, 220, 255), 2)
    cv2.putText(img, f'ROI x={rx} w={rw}', (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,255), 1)
    cv2.putText(img, f'ignore top={top_ig} bot={bot_ig}px', (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,220,255), 1)
    out = f'data/debug_roi_{name}.png'
    cv2.imwrite(out, img)
    print(f'saved {out}')
