import argparse
from pathlib import Path


def cmd_build_pattern(args: argparse.Namespace) -> int:
    from src.patterns.pattern_build import build_pattern_from_image

    out = build_pattern_from_image(
        model=args.model,
        img_path=args.img,
        threshold=90,
        min_area=80,
        circularity_min=0.6,
    )
    print(f"[build-pattern] saved: {out}")
    return 0


def cmd_run_image(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np

    from src.io.load_images import load_bgr_image
    from src.io.save_results import save_image
    from src.pipeline.preprocess import preprocess_for_holes
    from src.pipeline.detect_holes import detect_holes_from_mask
    from src.pipeline.compare import compare_missing_only
    from src.pipeline.annotate import draw_compare_overlay
    from src.patterns.pattern_io import load_pattern, pattern_path
    from src.patterns.roi import load_roi, apply_roi
    from src.pipeline.align_edge import align_image_by_right_edge

    # 1) cargar patrón
    pat_file = pattern_path(args.model)
    pattern = load_pattern(pat_file)

    # 2) cargar imagen
    img_full = load_bgr_image(args.img)

    # 3) alinear por borde derecho (rotación)
    img_aligned, align_res = align_image_by_right_edge(img_full)
    print(f"[align] angle_deg={align_res.angle_deg:.2f} lines={align_res.used_lines}")

    # 4) aplicar ROI si existe
    roi = load_roi(args.model)
    if roi is not None:
        img = apply_roi(img_aligned, roi)
    else:
        img = img_aligned

    # 5) detección rápida para obtener centroide detectado (para traslación)
    gray0 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask0 = preprocess_for_holes(gray0, threshold=90)
    holes0 = detect_holes_from_mask(mask0, min_area=80, circularity_min=0.6)

    if len(holes0) >= 10 and len(pattern.points) >= 10:
        det_np = np.array([(h.x, h.y) for h in holes0], dtype=float)
        pat_np = np.array(pattern.points, dtype=float)

        c_det = det_np.mean(axis=0)
        c_pat = pat_np.mean(axis=0)
        shift = c_pat - c_det  # (dx, dy)

        # aplicar traslación SIN escalar
        M = np.array([[1.0, 0.0, float(shift[0])],
                      [0.0, 1.0, float(shift[1])]], dtype=float)

        img = cv2.warpAffine(
            img,
            M,
            (img.shape[1], img.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE
        )
        print(f"[shift] dx={shift[0]:.2f} dy={shift[1]:.2f}")
    else:
        print("[shift] skipped (not enough points)")

    # 6) detección final (ya rotada + centrada)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = preprocess_for_holes(gray, threshold=90)
    holes = detect_holes_from_mask(mask, min_area=80, circularity_min=0.6)
    detected_points = [(h.x, h.y) for h in holes]

    # 7) comparar (solo missing)
    # Para celular suele convenir un poco más de tolerancia:
    report = compare_missing_only(pattern.points, detected_points, tol_xy_px=12.0)

    overlay = draw_compare_overlay(img, holes, report.missing_points, report.status)

    print(
        f"[run-image] model={args.model} expected={report.expected} detected={report.detected} "
        f"missing={report.missing} status={report.status}"
    )

    if args.show:
        cv2.imshow("mask", mask)
        cv2.imshow("overlay", overlay)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    if args.save:
        out_dir = Path("data/output/ok") if report.status == "OK" else Path("data/output/nok")
        dbg_dir = Path("data/output/debug")
        save_image(dbg_dir / f"{args.img.stem}_mask.png", mask)
        save_image(out_dir / f"{args.img.stem}_overlay.png", overlay)
        print(f"[saved] {out_dir}")

    return 0


def cmd_run_folder(args: argparse.Namespace) -> int:
    print("[run-folder] (pendiente)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app_defyvision_metalconf",
        description="MVP CLI: inspección de patrón de agujeros (OK/NOK).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("build-pattern", help="Construir patrón (holes.json) desde imagen OK.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen OK de referencia.")
    sp.set_defaults(func=cmd_build_pattern)

    sp = sub.add_parser("run-image", help="Procesar una imagen contra un patrón.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen a procesar.")
    sp.add_argument("--show", action="store_true", help="Mostrar ventanas de debug (OpenCV).")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_image)

    sp = sub.add_parser("run-folder", help="Procesar una carpeta completa contra un patrón.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--input", required=True, type=Path, help="Carpeta con imágenes a procesar.")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_folder)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())