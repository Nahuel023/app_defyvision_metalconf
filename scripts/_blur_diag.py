"""Diagnose blur scores across esterilla folder."""
import cv2, numpy as np, json, sys, os
sys.path.insert(0, '.')
from src.pipeline.preprocess import preprocess_for_holes
from src.utils.config import load_tolerances

folder = r'C:\Users\DefyC\Downloads\05-06-2026-PATRONES INICIALES\05-06-2026-ESTERILLA_1'
with open('data/patterns/modelo_A/roi.json') as f:
    roi = json.load(f)
tols = load_tolerances('modelo_A')

rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
frames = sorted(f for f in os.listdir(folder) if f.endswith('.png'))

scores = []
for fname in frames:
    img = cv2.imread(os.path.join(folder, fname))
    roi_img = img[ry:ry+rh, rx:rx+rw]
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    scores.append((fname, score))

scores.sort(key=lambda x: x[1])
print("=== 20 frames con MENOR nitidez (mas borrosos) ===")
for fname, s in scores[:20]:
    print(f"  {fname}: {s:.0f}")

print("\n=== Frames 1 y 15 especificamente ===")
for fname, s in scores:
    if fname in ('frame_0001.png', 'frame_0015.png'):
        print(f"  {fname}: {s:.0f}")

arr = np.array([s for _, s in scores])
print(f"\n=== Estadisticas de los {len(scores)} frames ===")
print(f"  min={arr.min():.0f}  p5={np.percentile(arr,5):.0f}  p10={np.percentile(arr,10):.0f}")
print(f"  p25={np.percentile(arr,25):.0f}  median={np.median(arr):.0f}  p75={np.percentile(arr,75):.0f}")
print(f"  p90={np.percentile(arr,90):.0f}  p95={np.percentile(arr,95):.0f}  max={arr.max():.0f}")
print(f"\n  blur_score_min actual: {tols.get('blur_score_min', 'N/A')}")
print(f"  Frames bajo threshold actual: {sum(1 for _, s in scores if s < float(tols.get('blur_score_min', 0)))}")
