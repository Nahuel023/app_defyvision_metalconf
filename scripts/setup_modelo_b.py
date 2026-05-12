"""
Setup completo de modelo_B:
1. Crea ROI basado en bounds reales de agujeros
2. Construye patron desde frame_0000
3. Valida con muestra de frames
"""
import sys, os, json
os.environ["PYTHONIOENCODING"] = "utf-8"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

REC_DIR = Path("data/recordings/20260512_194928")
REF_FRAME = REC_DIR / "frame_0000.png"
ROI_PATH  = Path("data/patterns/modelo_B/roi.json")
MODEL     = "modelo_B"

# Bounds observados + margen conservador
ROI_X1, ROI_X2 = 860, 1230   # 901-40 a 1172+58 (margen 40px izq, 58px der)
ROI_Y1, ROI_Y2 = 0,   1080   # altura completa

def step1_create_roi():
    print("=== PASO 1: Crear ROI modelo_B ===")
    ROI_PATH.parent.mkdir(parents=True, exist_ok=True)
    roi = {"x1": ROI_X1, "y1": ROI_Y1, "x2": ROI_X2, "y2": ROI_Y2}
    with open(ROI_PATH, "w") as f:
        json.dump(roi, f, indent=2)
    print(f"  ROI guardado: x=[{ROI_X1},{ROI_X2}] y=[{ROI_Y1},{ROI_Y2}]")
    print(f"  Size ROI: {ROI_X2-ROI_X1}x{ROI_Y2-ROI_Y1}px  (de 1920x1080)")


def step2_build_pattern():
    print("\n=== PASO 2: Construir patron modelo_B ===")
    from src.patterns.pattern_build import build_pattern_from_image
    out = build_pattern_from_image(MODEL, REF_FRAME)
    print(f"  Patron guardado: {out}")

    from src.patterns.pattern_io import load_pattern, pattern_path
    pat = load_pattern(pattern_path(MODEL))
    print(f"  Puntos: {len(pat.points)}")
    print(f"  image_size: {pat.image_size}")
    print(f"  dx={pat.dx}  dy={pat.dy}")
    print(f"  phase=({pat.phase_x},{pat.phase_y})")
    xs = [p[0] for p in pat.points]
    ys = [p[1] for p in pat.points]
    print(f"  X rango: {min(xs):.0f} - {max(xs):.0f}")
    print(f"  Y rango: {min(ys):.0f} - {max(ys):.0f}")


def step3_validate():
    print("\n=== PASO 3: Validar patron sobre muestra de frames ===")
    from src.inspection import inspect_image
    frames = sorted(REC_DIR.glob("frame_*.png"))

    # Candidatos: frames con ~297 holes (ya identificados)
    sample_indices = [0, 1, 2, 3, 12, 14, 20, 21, 22, 23, 30, 31, 52, 57, 103]
    print(f"  {'IDX':>5}  {'Status':>6}  {'Missing':>8}  {'Detected':>9}  {'ShiftX':>8}  {'ShiftY':>8}")
    results = []
    for idx in sample_indices:
        if idx >= len(frames):
            continue
        r = inspect_image(MODEL, frames[idx])
        sx = f"{r.shift_xy[0]:.1f}" if r.shift_xy else "N/A"
        sy = f"{r.shift_xy[1]:.1f}" if r.shift_xy else "N/A"
        detected = len(r.holes)
        results.append(r)
        print(f"  {idx:>5}  {r.status:>6}  {r.report.missing:>8}  {detected:>9}  {sx:>8}  {sy:>8}")

    ok  = sum(1 for r in results if r.status == "OK")
    nok = len(results) - ok
    missings = [r.report.missing for r in results]
    print(f"\n  Resultado muestra: OK={ok}  NOK={nok}  "
          f"missing_med={np.median(missings):.1f}  missing_max={max(missings)}")

    # Si hay muchos missing, probar con tol_xy_px mas grande
    if np.median(missings) > 2:
        print("\n  Probando tol_xy_px mayor para ver sensibilidad...")
        from src.inspection import _inspect_bgr
        from src.utils.config import load_tolerances
        from src.io.load_images import load_bgr_image
        for tol in [22, 28, 35, 45]:
            # Patch tolerances temporarily
            tols = load_tolerances(MODEL)
            tols["tol_xy_px"] = tol
            # Can't easily patch, just report
        print("  (usa 'tol_xy_px' en tolerancias.yaml seccion modelo_B para ajustar)")


def step4_full_scan():
    print("\n=== PASO 4: Scan completo (todos los frames) ===")
    from src.inspection import inspect_image
    frames = sorted(REC_DIR.glob("frame_*.png"))
    ok_count = nok_count = 0
    all_missing = []
    for idx, p in enumerate(frames):
        r = inspect_image(MODEL, p)
        all_missing.append(r.report.missing)
        if r.status == "OK":
            ok_count += 1
        else:
            nok_count += 1

    total = ok_count + nok_count
    print(f"  Total: {total}  OK: {ok_count} ({ok_count/total*100:.0f}%)  "
          f"NOK: {nok_count} ({nok_count/total*100:.0f}%)")
    print(f"  Missing: med={np.median(all_missing):.1f}  "
          f"p75={np.percentile(all_missing,75):.1f}  "
          f"max={max(all_missing)}")

    # Distribucion de missing
    from collections import Counter
    dist = Counter(all_missing)
    print("  Distribucion missing (top 15):")
    for missing, cnt in sorted(dist.items())[:15]:
        bar = "#" * (cnt * 30 // total)
        print(f"    {missing:>3} missing: {cnt:>3} frames  {bar}")


if __name__ == "__main__":
    step1_create_roi()
    step2_build_pattern()
    step3_validate()
    step4_full_scan()
