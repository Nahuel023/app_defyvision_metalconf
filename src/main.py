import argparse
from pathlib import Path


def _show_scaled_window(name: str, image, max_width: int = 1280, max_height: int = 900) -> None:
    import cv2

    h, w = image.shape[:2]
    scale = min(max_width / w, max_height / h, 1.0)
    win_w = max(1, int(w * scale))
    win_h = max(1, int(h * scale))

    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(name, win_w, win_h)
    cv2.imshow(name, image)


def cmd_build_pattern(args: argparse.Namespace) -> int:
    from src.patterns.pattern_build import build_pattern_from_image

    out = build_pattern_from_image(model=args.model, img_path=args.img)
    print(f"[build-pattern] saved: {out}")
    return 0


def cmd_run_image(args: argparse.Namespace) -> int:
    import cv2

    from src.inspection import inspect_image

    result = inspect_image(args.model, args.img, save=args.save)
    print(f"[align] angle_deg={result.angle_deg:.2f} lines={result.used_lines}")
    if result.shift_xy is None:
        print("[shift] skipped (not enough points)")
    else:
        print(f"[shift] dx={result.shift_xy[0]:.2f} dy={result.shift_xy[1]:.2f}")

    print(
        f"[run-image] model={args.model} expected={result.report.expected} "
        f"detected={result.report.detected} missing={result.report.missing} status={result.status}"
    )

    if args.show:
        _show_scaled_window("mask", result.mask)
        _show_scaled_window("overlay", result.overlay)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    if args.save:
        out_dir = Path("data/output/ok") if result.status == "OK" else Path("data/output/nok")
        print(f"[saved] {out_dir}")

    return 0


def cmd_run_folder(args: argparse.Namespace) -> int:
    from src.inspection import inspect_folder

    summary = inspect_folder(
        args.model,
        args.input,
        save=args.save,
        frame_rate_hz=args.fps,
    )
    print(
        f"[run-folder] model={args.model} total={summary.total} "
        f"raw_ok={summary.ok} raw_nok={summary.nok} "
        f"temporal_ok={summary.temporal_ok} temporal_nok={summary.temporal_nok}"
    )
    print(
        f"[temporal] consecutive_nok_frames={summary.consecutive_nok_frames} "
        f"fps={summary.frame_rate_hz:.2f} response_time_sec={summary.response_time_sec:.2f} "
        f"max_response_sec={summary.max_response_sec:.2f} "
        f"meets_target={summary.meets_response_target}"
    )
    for temporal in summary.temporal_results:
        result = temporal.result
        print(
            f"  - {result.image_path.name}: raw={result.status} temporal={temporal.decision_status} "
            f"streak={temporal.nok_streak} missing={result.report.missing} detected={result.report.detected}"
        )
    return 0


def cmd_gui(_: argparse.Namespace) -> int:
    from src.gui_app import launch_gui

    launch_gui()
    return 0


def cmd_operator_ui(_: argparse.Namespace) -> int:
    from src.qt_operator_app import launch_operator_ui

    launch_operator_ui()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app_defyvision_metalconf",
        description="MVP CLI: inspeccion de patron de agujeros (OK/NOK).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("build-pattern", help="Construir patron (holes.json) desde imagen OK.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen OK de referencia.")
    sp.set_defaults(func=cmd_build_pattern)

    sp = sub.add_parser("run-image", help="Procesar una imagen contra un patron.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen a procesar.")
    sp.add_argument("--show", action="store_true", help="Mostrar ventanas de debug (OpenCV).")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_image)

    sp = sub.add_parser("run-folder", help="Procesar una carpeta completa contra un patron.")
    sp.add_argument("--model", required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--input", required=True, type=Path, help="Carpeta con imagenes a procesar.")
    sp.add_argument("--fps", type=float, default=None, help="FPS efectivo de la secuencia para decision temporal.")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_folder)

    sp = sub.add_parser("gui", help="Abrir interfaz minima en tkinter.")
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser("operator-ui", help="Abrir interfaz de operador en PyQt.")
    sp.set_defaults(func=cmd_operator_ui)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
