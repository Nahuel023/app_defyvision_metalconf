"""
Analisis completo de frames modelo_B.
"""
import sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from src.utils.config import load_tolerances
from src.pipeline.preprocess import preprocess_for_holes
from src.pipeline.detect_holes import detect_holes_from_mask
from src.pipeline.align_edge import align_image_by_right_edge

REC_DIR = Path("data/recordings/20260512_194928")

def detect(img, tols, margin=0.0):
    mask = preprocess_for_holes(
        img, threshold=int(tols["threshold"]),
        use_channel=str(tols["use_channel"]),
        polarity=str(tols["polarity"]),
        use_clahe=bool(tols.get("use_clahe", False)),
        clahe_clip=float(tols.get("clahe_clip", 2.0)),
        clahe_tile=int(tols.get("clahe_tile", 8)),
        use_otsu=bool(tols.get("use_otsu", False)),
    )
    return detect_holes_from_mask(
        mask, min_area=float(tols["min_area"]),
        circularity_min=float(tols["circularity_min"]),
        aspect_ratio_max=float(tols["aspect_ratio_max"]),
        edge_margin_px=margin,
    ), mask

def analyze():
    frames = sorted(REC_DIR.glob("frame_*.png"))
    print(f"Frames: {len(frames)}  @10fps => duracion aprox {len(frames)/10:.1f}s")

    tols = load_tolerances()

    # ── 1. Scan completo ──────────────────────────────────────────────────────
    print("\n=== SCAN COMPLETO ===")
    print(f"{'IDX':>5}  {'Holes':>6}  {'Diff-prev':>10}  {'Diff-last-insp':>15}")
    all_data = []
    prev_gray = None
    last_insp_gray = None
    diffs_prev = []
    diffs_last = []
    for idx, p in enumerate(frames):
        img = cv2.imread(str(p))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        holes, _ = detect(img, tols)
        d_prev = float(np.mean(cv2.absdiff(gray, prev_gray))) if prev_gray is not None else 0.0
        d_last = float(np.mean(cv2.absdiff(gray, last_insp_gray))) if last_insp_gray is not None else 0.0
        diffs_prev.append(d_prev)
        diffs_last.append(d_last)
        all_data.append({"idx": idx, "path": p, "holes": len(holes), "d_prev": d_prev, "d_last": d_last})
        if d_last > 8.0 or last_insp_gray is None:
            last_insp_gray = gray
        prev_gray = gray

    counts = [d["holes"] for d in all_data]
    mode_count = max(set(counts), key=counts.count)
    print(f"  mode={mode_count}  med={int(np.median(counts))}  "
          f"min={min(counts)}  max={max(counts)}  std={np.std(counts):.1f}")
    print(f"  diff_prev: med={np.median(diffs_prev):.2f}  max={max(diffs_prev):.2f}")
    print(f"  diff_last: med={np.median(diffs_last):.2f}  max={max(diffs_last):.2f}")

    # Mostrar todos los frames
    print(f"\n{'IDX':>5}  {'Holes':>6}  {'DiffPrev':>9}  {'DiffLast':>9}  {'Note':}")
    for d in all_data:
        note = ""
        if abs(d["holes"] - mode_count) > 10:
            note = " <-- PARCIAL/TRANSICION"
        elif abs(d["holes"] - mode_count) <= 3:
            note = " ** buen candidato **"
        print(f"  {d['idx']:>3}  {d['holes']:>6}  {d['d_prev']:>9.2f}  {d['d_last']:>9.2f}  {note}")

    # ── 2. Mejores candidatos ─────────────────────────────────────────────────
    print("\n=== MEJORES CANDIDATOS REFERENCIA (holes aprox moda, primeros frames) ===")
    candidates = [d for d in all_data if abs(d["holes"] - mode_count) <= 3]
    print(f"  Total candidatos: {len(candidates)}")
    best = sorted(candidates, key=lambda d: d["idx"])[:15]
    for d in best:
        print(f"  Frame {d['idx']:>4}  {d['path'].name}  holes={d['holes']}")

    # ── 3. Geometria: espaciado entre agujeros ────────────────────────────────
    print("\n=== GEOMETRIA: ESPACIADO (frame de referencia candidato) ===")
    if candidates:
        ref_frame = candidates[0]["path"]
        img_ref = cv2.imread(str(ref_frame))
        img_aligned, align_res = align_image_by_right_edge(img_ref)
        print(f"  Alineacion: angle={align_res.angle_deg:.2f}deg  lines={align_res.used_lines}")
        holes, _ = detect(img_aligned, tols)
        print(f"  Holes detectados: {len(holes)}")
        if holes:
            xs = np.array([h.x for h in holes])
            ys = np.array([h.y for h in holes])
            radii = np.array([h.r for h in holes])
            print(f"  Radio: med={np.median(radii):.1f}  min={radii.min():.1f}  max={radii.max():.1f}")
            print(f"  X rango: {xs.min():.0f} - {xs.max():.0f}")
            print(f"  Y rango: {ys.min():.0f} - {ys.max():.0f}")

            # Estimar espaciado
            from src.pipeline.grid_fitting import estimate_spacing, estimate_phase
            dx = estimate_spacing(xs)
            dy = estimate_spacing(ys)
            px = estimate_phase(xs, dx)
            py = estimate_phase(ys, dy)
            print(f"  Grid: dx={dx:.1f}  dy={dy:.1f}  phase=({px:.1f},{py:.1f})")

            # Distribucion X e Y
            xs_sorted = np.sort(xs)
            ys_sorted = np.sort(ys)
            x_diffs = np.diff(xs_sorted)
            y_diffs = np.diff(ys_sorted)
            # Filtrar solo diffs pequenas (dentro de la misma columna)
            x_small = x_diffs[x_diffs < dx*0.7] if dx > 0 else x_diffs
            y_small = y_diffs[y_diffs < dy*0.7] if dy > 0 else y_diffs
            if len(x_small) > 0:
                print(f"  Spacing X (intra-col): med={np.median(x_small):.1f}  std={np.std(x_small):.1f}")
            if len(y_small) > 0:
                print(f"  Spacing Y (intra-row): med={np.median(y_small):.1f}  std={np.std(y_small):.1f}")

    # ── 4. Recomendaciones de threshold ──────────────────────────────────────
    print("\n=== RECOMENDACION continuous_position_threshold ===")
    print("  Diffs entre frames consecutivos a 10fps:")
    print(f"    min={min(diffs_prev):.2f}  max={max(diffs_prev):.2f}  "
          f"med={np.median(diffs_prev):.2f}  p75={np.percentile(diffs_prev,75):.2f}  "
          f"p95={np.percentile(diffs_prev,95):.2f}")
    print("  A 30fps el movimiento por frame sera ~1/3:")
    print(f"    min~{min(diffs_prev)/3:.2f}  max~{max(diffs_prev)/3:.2f}  med~{np.median(diffs_prev)/3:.2f}")
    print("  Umbral recomendado (acumular 2-3 frames de avance a 30fps):")
    for t in [2.0, 3.0, 4.0, 5.0, 6.0]:
        sim_insp = sum(1 for d in diffs_last if d >= t)
        print(f"    thr={t:.1f}: ~{sim_insp} inspecciones simuladas en esta grabacion")

    # ── 5. Inspeccion con modelo_B actual ─────────────────────────────────────
    print("\n=== INSPECCION CON MODELO_B ACTUAL ===")
    try:
        from src.inspection import inspect_image
        sample_idxs = [d["idx"] for d in candidates[:5]]
        for idx in sample_idxs:
            p = frames[idx]
            r = inspect_image("modelo_B", p)
            print(f"  Frame {idx:>4}  status={r.status}  missing={r.report.missing}  "
                  f"shift=({r.shift_xy[0] if r.shift_xy else 'N/A':.1f},{r.shift_xy[1] if r.shift_xy else 'N/A':.1f})")
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    analyze()
