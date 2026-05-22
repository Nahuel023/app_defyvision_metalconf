"""Calibración de blur_score_min: muestra varianza del Laplaciano por frame.

Uso:
    python scripts/_debug_blur_score.py <carpeta>

Muestra la distribución de blur_score en todos los frames de la carpeta,
para ayudar a elegir un valor de blur_score_min en tolerancias.yaml.

Valores típicos (modelo_B, backlight encendido):
  Frames nítidos (buenas capturas): >> 200
  Frames borrosos (blur de movimiento): < 100-150
  Frames muy borrosos o sin material: < 50
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cv2
import numpy as np
from pathlib import Path
from src.io.load_images import load_bgr_image
from src.inspection import iter_image_files

if len(sys.argv) < 2:
    print("Uso: python scripts/_debug_blur_score.py <carpeta>")
    sys.exit(1)

folder = Path(sys.argv[1])
if not folder.is_dir():
    print(f"Error: {folder} no es un directorio")
    sys.exit(1)

frames = list(iter_image_files(folder))
if not frames:
    print("No se encontraron imágenes.")
    sys.exit(1)

scores = []
for img_path in frames:
    img = load_bgr_image(img_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    scores.append((img_path.name, score))

scores_vals = [s for _, s in scores]
print(f"Frames analizados: {len(scores)}")
print(f"blur_score — min={min(scores_vals):.1f}  p10={np.percentile(scores_vals, 10):.1f}  "
      f"p25={np.percentile(scores_vals, 25):.1f}  median={np.median(scores_vals):.1f}  "
      f"p75={np.percentile(scores_vals, 75):.1f}  max={max(scores_vals):.1f}")

# Histograma
bins = [0, 25, 50, 75, 100, 150, 200, 300, 500, 1000, 5000]
print("\nHistograma de blur_score:")
for i in range(len(bins) - 1):
    c = sum(1 for v in scores_vals if bins[i] <= v < bins[i+1])
    if c:
        bar = "#" * min(c, 40)
        print(f"  {bins[i]:5d}-{bins[i+1]:5d}: {c:4d}  {bar}")

# Los 10 frames con menor blur_score (más borrosos)
print("\nFrames con menor blur_score (más borrosos):")
for name, score in sorted(scores, key=lambda x: x[1])[:10]:
    print(f"  {name}: {score:.1f}")

print("\nSugerencia: blur_score_min = percentil 10 a 25 de los frames borrosos reales.")
print("Deja en 0.0 si no hay blur de movimiento significativo o para deshabilitar.")
