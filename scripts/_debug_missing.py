"""
Diagnóstico de agujeros marcados como 'missing' pero que tienen un detectado cercano.

Muestra:
- Para cada expected_point 'missing': distancia al detectado más cercano
- Si esa distancia < 2*tol_xy_px, es candidato a falso-missing por tolerancia o fase off
- Guarda una imagen con:
    - Círculos verdes = detectados que matchearon
    - Cruces rojas = missing sin detectado cercano
    - Cruces amarillas = missing CON detectado cercano (<2×tol) — falso missing
    - Líneas cyan = la línea entre el missing y su detectado más cercano
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cv2
import numpy as np
from pathlib import Path

from src.inspection import _inspect_bgr
from src.io.load_images import load_bgr_image
from src.patterns.pattern_io import load_pattern, find_pattern_path
from src.patterns.roi import load_roi
from src.utils.config import load_tolerances

MODEL      = "modelo_B"
SCANNER_ID = "scanner_1"

# ── Elegí el frame problemático ───────────────────────────────────────────────
FRAME_PATH = Path("C:/Tadeo/METALCONF/imagenes_prueba_METALCONF/20260519_121741/frame_0001.png")
# Si no existe, busca el primer frame disponible de los datos de debug

if not FRAME_PATH.exists():
    candidates = list(Path("data/frames").glob("*.png")) + list(Path("data/frames").glob("*.jpg"))
    if not candidates:
        print("No se encontró el frame. Editar FRAME_PATH arriba.")
        sys.exit(1)
    FRAME_PATH = sorted(candidates)[0]

print(f"Analizando: {FRAME_PATH}")

tol = load_tolerances(MODEL)
tol_xy_px = float(tol["tol_xy_px"])

img_full = load_bgr_image(FRAME_PATH)
result = _inspect_bgr(MODEL, img_full, image_path=FRAME_PATH, scanner_id=SCANNER_ID)

report       = result.report
detected_pts = [(h.x, h.y) for h in result.holes]
missing_pts  = report.missing_points

print(f"\nResumen:")
print(f"  Expected:  {report.expected}")
print(f"  Detected:  {report.detected}")
print(f"  Missing:   {report.missing}")
print(f"  Extra:     {report.extra}")
print(f"  Status:    {report.status}")
print(f"  tol_xy_px: {tol_xy_px}")

if not missing_pts:
    print("\nNo hay missing — sin falsos negativos que analizar.")
    sys.exit(0)

if not detected_pts:
    print("\nNo hay detectados — problema de detección, no de matching.")
    sys.exit(0)

det_arr = np.array(detected_pts, dtype=np.float32)   # (n_det, 2)
mis_arr = np.array(missing_pts,  dtype=np.float32)   # (n_mis, 2)

# Para cada missing, distancia al detectado más cercano
diff = mis_arr[:, None, :] - det_arr[None, :, :]     # (n_mis, n_det, 2)
dists = np.sqrt((diff ** 2).sum(axis=2))              # (n_mis, n_det)
nearest_dist = dists.min(axis=1)                      # (n_mis,)
nearest_idx  = dists.argmin(axis=1)                   # (n_mis,)

print(f"\nDistancias de cada 'missing' al detectado más cercano:")
print(f"  {'#':>3}  {'exp_x':>6}  {'exp_y':>6}  {'det_x':>6}  {'det_y':>6}  {'dist':>7}  {'tipo'}")
print(f"  {'─'*60}")

false_missing = []
true_missing  = []

for i, ((ex, ey), dist, jj) in enumerate(zip(missing_pts, nearest_dist, nearest_idx)):
    dx_, dy_ = det_arr[jj]
    tipo = "FALSO (dentro de 2×tol)" if dist < tol_xy_px * 2 else "verdadero"
    if dist < tol_xy_px * 2:
        false_missing.append((ex, ey, float(dx_), float(dy_), float(dist)))
    else:
        true_missing.append((ex, ey, float(dist)))
    print(f"  {i:>3}  {ex:>6.1f}  {ey:>6.1f}  {dx_:>6.1f}  {dy_:>6.1f}  {dist:>7.1f}px  {tipo}")

print(f"\nResumen:")
print(f"  Falsos missing (detectado cercano dentro de 2×{tol_xy_px}px): {len(false_missing)}")
print(f"  Missing reales (sin detectado cerca):                         {len(true_missing)}")

if false_missing:
    dists_falsos = [d for *_, d in false_missing]
    print(f"\n  Distancias de falsos: min={min(dists_falsos):.1f}px  max={max(dists_falsos):.1f}px  "
          f"media={np.mean(dists_falsos):.1f}px")
    print(f"  → Aumentar tol_xy_px a >{max(dists_falsos):.0f}px para eliminarlos")
    print(f"    (verificar que sigue siendo < dx={tol.get('dx', 28)} para no crear ambigüedad)")

# ── Imagen de diagnóstico ─────────────────────────────────────────────────────
out = result.overlay.copy()

# Re-dibujar en la ROI del overlay
roi = None
try:
    from src.patterns.roi import load_roi
    roi = load_roi(MODEL, SCANNER_ID)
except Exception:
    pass

def to_full(x, y):
    if roi is not None:
        return int(x) + roi.x, int(y) + roi.y
    return int(x), int(y)

# Líneas cyan: missing → detectado más cercano
for (ex, ey, dx_, dy_, dist) in false_missing:
    fx1, fy1 = to_full(ex, ey)
    fx2, fy2 = to_full(dx_, dy_)
    cv2.line(out, (fx1, fy1), (fx2, fy2), (255, 255, 0), 2)

# Cruces amarillas sobre los falsos missing (encima de las rojas)
for (ex, ey, dx_, dy_, dist) in false_missing:
    fx, fy = to_full(ex, ey)
    cv2.drawMarker(out, (fx, fy), (0, 255, 255),
                   markerType=cv2.MARKER_TILTED_CROSS, markerSize=30, thickness=3)
    cv2.putText(out, f"{dist:.0f}px", (fx + 15, fy - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)

out_path = Path("data/debug_missing.png")
cv2.imwrite(str(out_path), out)
print(f"\nImagen guardada: {out_path}")
print("  Cruces AMARILLAS = falsos missing (detectado cercano, fuera de tolerancia)")
print("  Cruces ROJAS     = missing reales")
print("  Líneas CYAN      = conexión missing → detectado más cercano")
