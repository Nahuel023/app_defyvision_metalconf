"""
Diagnóstico por carpeta: exporta métricas por frame a CSV.

Uso:
    python scripts/run_folder_csv.py <carpeta_entrada> [opciones]

Opciones:
    --model    modelo a usar (default: modelo_B)
    --scanner  scanner_id   (default: scanner_1)
    --output   ruta del CSV de salida (default: <carpeta>_metrics.csv)

Columnas del CSV:
    frame                    nombre del archivo
    status                   OK / NOK
    expected                 posiciones esperadas por el patrón
    detected                 agujeros detectados en el frame
    missing                  esperados sin match dentro de tol_xy_px
    extra                    detectados sin posición esperada asignada
    detection_ratio          detected / expected (0–1)
    centering_offset_px      offset lateral chapa vs agujeros (0 si no aplica)
    alignment_ok             False = RANSAC/phase falló, usó fallback
    missing_nearest_max_px   distancia máxima de un missing al detectado más cercano
    missing_nearest_med_px   distancia mediana idem
    false_missing_count      missing con detectado cercano dentro de 2×tol_xy_px
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from pathlib import Path

from src.inspection import _inspect_bgr, iter_image_files
from src.io.load_images import load_bgr_image
from src.utils.config import load_tolerances


def analyse_frame(img_path: Path, model: str, scanner: str, tol_xy_px: float) -> dict:
    img = load_bgr_image(img_path)
    result = _inspect_bgr(model, img, image_path=img_path, scanner_id=scanner)
    r = result.report

    mis_nearest_max = 0.0
    mis_nearest_med = 0.0
    false_missing   = 0
    if r.missing_points and result.holes:
        det_pts = np.array([(h.x, h.y) for h in result.holes], dtype=np.float32)
        mis_pts = np.array(r.missing_points, dtype=np.float32)
        diff    = mis_pts[:, None, :] - det_pts[None, :, :]
        dists   = np.sqrt((diff ** 2).sum(axis=2)).min(axis=1)
        mis_nearest_max = float(dists.max())
        mis_nearest_med = float(np.median(dists))
        false_missing   = int((dists < tol_xy_px * 2).sum())

    centering_offset = (
        result.centering.offset_px if result.centering is not None else 0.0
    )

    return {
        "frame":                  img_path.name,
        "status":                 result.status,
        "expected":               r.expected,
        "detected":               r.detected,
        "missing":                r.missing,
        "extra":                  r.extra,
        "detection_ratio":        f"{result.detection_ratio:.3f}",
        "centering_offset_px":    f"{centering_offset:.1f}",
        "alignment_ok":           result.alignment_ok,
        "missing_nearest_max_px": f"{mis_nearest_max:.1f}",
        "missing_nearest_med_px": f"{mis_nearest_med:.1f}",
        "false_missing_count":    false_missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Análisis por carpeta → CSV de métricas por frame"
    )
    parser.add_argument("input", type=Path, help="Carpeta con frames")
    parser.add_argument("--model",   default="modelo_B",  help="Modelo de inspección")
    parser.add_argument("--scanner", default="scanner_1", help="scanner_id")
    parser.add_argument("--output",  type=Path, default=None, help="Ruta CSV de salida")
    args = parser.parse_args()

    if not args.input.is_dir():
        print(f"Error: {args.input} no es un directorio.")
        sys.exit(1)

    out_path = args.output or args.input.parent / f"{args.input.name}_metrics.csv"
    tol      = load_tolerances(args.model)
    tol_xy   = float(tol["tol_xy_px"])

    frames = list(iter_image_files(args.input))
    if not frames:
        print("No se encontraron imágenes en la carpeta.")
        sys.exit(1)

    print(f"Modelo:  {args.model} / scanner: {args.scanner}")
    print(f"Frames:  {len(frames)}")
    print(f"tol_xy:  {tol_xy}px")
    print(f"Salida:  {out_path}")
    print()

    fieldnames = [
        "frame", "status", "expected", "detected", "missing", "extra",
        "detection_ratio", "centering_offset_px", "alignment_ok",
        "missing_nearest_max_px", "missing_nearest_med_px", "false_missing_count",
    ]

    ok_count  = 0
    nok_count = 0
    rows: list[dict] = []

    for i, img_path in enumerate(frames, 1):
        row = analyse_frame(img_path, args.model, args.scanner, tol_xy)
        rows.append(row)
        if row["status"] == "OK":
            ok_count += 1
        else:
            nok_count += 1
        if i % 20 == 0 or i == len(frames):
            print(f"  {i}/{len(frames)}  OK={ok_count}  NOK={nok_count}")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Resumen rápido
    missings    = [int(r["missing"]) for r in rows]
    false_mis   = [int(r["false_missing_count"]) for r in rows]
    max_nearest = [float(r["missing_nearest_max_px"]) for r in rows if float(r["missing_nearest_max_px"]) > 0]

    print()
    print(f"Resumen:")
    print(f"  OK:  {ok_count} / {len(frames)}")
    print(f"  NOK: {nok_count} / {len(frames)}")
    print(f"  Missing: min={min(missings)}  max={max(missings)}  "
          f"media={sum(missings)/len(missings):.1f}")
    if false_mis:
        print(f"  Falsos missing (detectado cercano <2xtol): "
              f"max_por_frame={max(false_mis)}  total={sum(false_mis)}")
    if max_nearest:
        print(f"  Distancia missing->mas_cercano: "
              f"max_global={max(max_nearest):.1f}px  "
              f"media={sum(max_nearest)/len(max_nearest):.1f}px")
    print(f"\nCSV guardado: {out_path}")


if __name__ == "__main__":
    main()
