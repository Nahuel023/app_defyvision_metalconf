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


def cmd_define_roi(args: argparse.Namespace) -> int:
    """Selección interactiva de ROI sobre una imagen o frame de cámara."""
    import json
    import cv2

    if args.img:
        frame = cv2.imread(str(args.img))
        if frame is None:
            print(f"[define-roi] no se pudo leer: {args.img}")
            return 1
    else:
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print(f"[define-roi] no se pudo abrir cámara {args.camera}")
            return 1
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
        for _ in range(5):
            cap.read()
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            print("[define-roi] no se pudo capturar frame")
            return 1

    win = "Seleccionar ROI — arrastra con mouse, ENTER confirma, C cancela"
    h_img, w_img = frame.shape[:2]
    scale = min(1280 / w_img, 800 / h_img, 1.0)
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, max(1, int(w_img * scale)), max(1, int(h_img * scale)))
    print("Dibujá un rectángulo con el mouse. ENTER o ESPACIO para confirmar, C para cancelar.")
    x, y, w, h = (int(v) for v in cv2.selectROI(win, frame, fromCenter=False, showCrosshair=True))
    cv2.destroyAllWindows()

    if w == 0 or h == 0:
        print("[define-roi] cancelado o ROI vacío")
        return 1

    from src.patterns.roi import roi_path
    out_path = roi_path(args.model, args.scanner)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"x": x, "y": y, "w": w, "h": h}), encoding="utf-8")
    print(f"[define-roi] ROI guardado: x={x} y={y} w={w} h={h} → {out_path}")
    return 0


def cmd_build_pattern(args: argparse.Namespace) -> int:
    from src.patterns.pattern_build import build_pattern_from_image

    out = build_pattern_from_image(model=args.model, img_path=args.img,
                                   scanner_id=args.scanner)
    print(f"[build-pattern] saved: {out}")
    return 0


def cmd_run_image(args: argparse.Namespace) -> int:
    import cv2

    from src.inspection import inspect_image

    result = inspect_image(args.model, args.img, save=args.save,
                           scanner_id=args.scanner)
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
        scanner_id=args.scanner,
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



def cmd_run(_: argparse.Namespace) -> int:
    """Modo producción: inicia el sistema completo (PLC + cámaras + UI)."""
    from src.utils.logger import setup_logging
    from src.controller.system import InspectionSystem
    from src.ui.operator import launch_operator_ui as launch_production_ui

    setup_logging()

    system = InspectionSystem()
    system.connect_plc()        # intenta conectar; si falla, la UI lo mostrará
    system.start_cameras()      # intenta abrir cámaras; errores van al log de la UI

    launch_production_ui(system)
    system.shutdown()
    return 0


def cmd_service(_: argparse.Namespace) -> int:
    """Modo servicio: UI de diagnóstico con login (independiente del operador)."""
    import sys
    from PyQt6.QtWidgets import QApplication, QDialog
    from src.utils.logger import setup_logging
    from src.controller.system import InspectionSystem
    from src.ui.login_dialog import LoginDialog
    from src.ui.service import ServiceWindow

    setup_logging()

    app = QApplication.instance() or QApplication(sys.argv)

    dlg = LoginDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return 0

    system = InspectionSystem()
    system.connect_plc()
    system.start_cameras()

    win = ServiceWindow(system)
    win.show()
    app.exec()

    system.shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app_defyvision_metalconf",
        description="MVP CLI: inspeccion de patron de agujeros (OK/NOK).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("define-roi", help="Seleccionar ROI interactivamente desde imagen o cámara.")
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_A).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_2). Si se omite, ROI compartida.")
    sp.add_argument("--img",     type=Path, default=None, help="Imagen existente; si se omite usa la cámara.")
    sp.add_argument("--camera",  type=int,  default=0,    help="Índice de cámara (default 0).")
    sp.set_defaults(func=cmd_define_roi)

    sp = sub.add_parser("build-pattern", help="Construir patron (holes.json) desde imagen OK.")
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_2). Si se omite, patrón compartido.")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen OK de referencia.")
    sp.set_defaults(func=cmd_build_pattern)

    sp = sub.add_parser("run-image", help="Procesar una imagen contra un patron.")
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner para resolver patrón específico.")
    sp.add_argument("--img", required=True, type=Path, help="Ruta a imagen a procesar.")
    sp.add_argument("--show", action="store_true", help="Mostrar ventanas de debug (OpenCV).")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_image)

    sp = sub.add_parser("run-folder", help="Procesar una carpeta completa contra un patron.")
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner para resolver patrón específico.")
    sp.add_argument("--input", required=True, type=Path, help="Carpeta con imagenes a procesar.")
    sp.add_argument("--fps", type=float, default=None, help="FPS efectivo de la secuencia para decision temporal.")
    sp.add_argument("--save", action="store_true", help="Guardar resultados en data/output.")
    sp.set_defaults(func=cmd_run_folder)

    sp = sub.add_parser("run", help="Modo producción: PLC + cámaras + UI en tiempo real.")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("service", help="Modo servicio: diagnóstico PLC/cámaras/logs con login.")
    sp.set_defaults(func=cmd_service)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
