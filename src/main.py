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
    import yaml

    if args.img:
        frame = cv2.imread(str(args.img))
        if frame is None:
            print(f"[define-roi] no se pudo leer: {args.img}")
            return 1
    else:
        from src.utils.camera_config import load_camera_settings
        cam_s  = load_camera_settings(args.scanner) if args.scanner else {}
        width  = int(cam_s.get("width",  1280))
        height = int(cam_s.get("height",  720))
        fps    = int(cam_s.get("fps",      30))

        # Encender backlight via PLC si está configurado para el scanner
        plc_client = None
        backlight_offset = None
        if args.scanner:
            try:
                io_cfg = yaml.safe_load(Path("config/io_map.yaml").read_text(encoding="utf-8"))
                plc_cfg = io_cfg.get("plc", {})
                sc_cfg  = io_cfg.get(args.scanner, {})
                backlight_offset = sc_cfg.get("outputs", {}).get("backlight")
                if backlight_offset is not None and plc_cfg.get("ip"):
                    from src.plc.client import PLCClient
                    plc_client = PLCClient(
                        ip=plc_cfg["ip"],
                        port=plc_cfg.get("port", 502),
                        unit_id=plc_cfg.get("unit_id", 1),
                    )
                    if plc_client.connect():
                        plc_client.write_coil(backlight_offset, True)
                        print(f"[define-roi] backlight encendido (Y offset={backlight_offset})")
                    else:
                        print("[define-roi] PLC no disponible — backlight no encendido")
                        plc_client = None
            except Exception as exc:
                print(f"[define-roi] backlight: {exc}")
                plc_client = None

        try:
            _FOURCC_MJPEG = cv2.VideoWriter.fourcc('M', 'J', 'P', 'G')
            cap = cv2.VideoCapture(args.camera, cv2.CAP_MSMF)
            if not cap.isOpened():
                print(f"[define-roi] no se pudo abrir cámara {args.camera}")
                return 1
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FOURCC, _FOURCC_MJPEG)
            cap.set(cv2.CAP_PROP_FPS,    fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(5):
                cap.read()
            ok, frame = cap.read()
            cap.release()
        finally:
            if plc_client is not None:
                plc_client.write_coil(backlight_offset, False)
                plc_client.disconnect()
                print("[define-roi] backlight apagado")

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
        f"[run-image] model={args.model}  status={result.status}\n"
        f"  expected={result.report.expected}  detected={result.report.detected}  "
        f"missing={result.report.missing}  extra={result.report.extra}\n"
        f"  detection_ratio={result.detection_ratio:.0%}  alignment_ok={result.alignment_ok}"
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
    align_fails = sum(1 for t in summary.temporal_results if not t.result.alignment_ok)
    avg_ratio   = sum(t.result.detection_ratio for t in summary.temporal_results) / max(1, summary.total)
    print(
        f"[quality]  avg_detection_ratio={avg_ratio:.0%}  "
        f"align_failures={align_fails}/{summary.total}"
    )
    for temporal in summary.temporal_results:
        result = temporal.result
        warn = ""
        if result.detection_ratio < 0.50:
            warn += " DETECCION_BAJA"
        if not result.alignment_ok:
            warn += " ALIGN_FALLBACK"
        if result.capture_quality_degraded:
            warn += " CALIDAD_DEGRADADA"
        print(
            f"  {result.image_path.name}: {result.status}/{temporal.decision_status}"
            f"  streak={temporal.nok_streak}"
            f"  missing={result.report.missing}  extra={result.report.extra}"
            f"  ratio={result.detection_ratio:.0%}{warn}"
        )
    return 0



def cmd_missing_folder(args: argparse.Namespace) -> int:
    """Diagnostic: scan folder and export frames where missing >= min_missing.

    production_status  — normal pipeline result (grid_max_missing applies).
    missing_status     — FALTANTE when report.missing >= min_missing, else OK.

    Exports:
      <output>/missing_overlays/frame_NNNN_missing_M_overlay.png  (one per FALTANTE frame)
      <output>/missing_report.csv

    This command does NOT touch PLC logic, production thresholds, or grid_max_missing.
    """
    import csv
    import cv2
    import numpy as np

    from src.inspection import inspect_image, iter_image_files
    from src.utils.config import load_tolerances

    input_dir: Path = args.input
    output_dir: Path = args.output
    min_missing: int = args.min_missing
    model: str = args.model
    scanner: str | None = args.scanner

    if not input_dir.is_dir():
        print(f"[missing-folder] ERROR: {input_dir} no es un directorio")
        return 1

    tol_xy_px = float(load_tolerances(model).get("tol_xy_px", 22.0))

    overlay_dir = output_dir / "missing_overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    frames = list(iter_image_files(input_dir))
    if not frames:
        print("[missing-folder] No se encontraron imágenes.")
        return 1

    print(f"[missing-folder] model={model}  scanner={scanner or '(global)'}")
    print(f"[missing-folder] frames={len(frames)}  min_missing={min_missing}  tol_xy={tol_xy_px}px")
    print(f"[missing-folder] salida -> {output_dir}")
    print()

    fieldnames = [
        "frame", "production_status", "missing_status",
        "expected", "detected", "missing", "extra",
        "detection_ratio", "alignment_ok",
        "false_missing_count", "missing_nearest_med_px", "missing_nearest_max_px",
    ]

    rows: list[dict] = []
    faltante_frames: list[tuple[str, int]] = []  # (name, missing_count)

    for idx, img_path in enumerate(frames, 1):
        result = inspect_image(model, img_path, save=False, scanner_id=scanner)
        r = result.report

        # Missing nearest-distance metrics (diagnose false missing)
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
            false_missing   = int((dists < tol_xy_px * 2.0).sum())

        missing_status = "FALTANTE" if r.missing >= min_missing else "OK"

        rows.append({
            "frame":                  img_path.name,
            "production_status":      result.status,
            "missing_status":         missing_status,
            "expected":               r.expected,
            "detected":               r.detected,
            "missing":                r.missing,
            "extra":                  r.extra,
            "detection_ratio":        f"{result.detection_ratio:.3f}",
            "alignment_ok":           result.alignment_ok,
            "false_missing_count":    false_missing,
            "missing_nearest_med_px": f"{mis_nearest_med:.1f}",
            "missing_nearest_max_px": f"{mis_nearest_max:.1f}",
        })

        if r.missing >= min_missing:
            faltante_frames.append((img_path.name, r.missing))
            # Draw "FALTANTE: N" badge on overlay and save
            ov = result.overlay.copy()
            label = f"FALTANTE: {r.missing}"
            font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 1.1, 3
            (tw, th), bl = cv2.getTextSize(label, font, scale, thick)
            h_ov, w_ov = ov.shape[:2]
            bx1, by1 = w_ov // 2 - tw // 2 - 10, 110
            bx2, by2 = w_ov // 2 + tw // 2 + 10, 110 + th + bl + 12
            cv2.rectangle(ov, (bx1, by1), (bx2, by2), (0, 60, 160), -1)
            cv2.rectangle(ov, (bx1, by1), (bx2, by2), (0, 80, 255), 2)
            cv2.putText(ov, label, (bx1 + 10, by2 - bl - 6),
                        font, scale, (255, 255, 255), thick, cv2.LINE_AA)
            stem = img_path.stem
            out_name = f"{stem}_missing_{r.missing}_overlay.png"
            cv2.imwrite(str(overlay_dir / out_name), ov)

        if idx % 20 == 0 or idx == len(frames):
            n_faltante = sum(1 for row in rows if row["missing_status"] == "FALTANTE")
            print(f"  {idx}/{len(frames)}  FALTANTE={n_faltante}")

    # Write CSV
    csv_path = output_dir / "missing_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Console summary
    total = len(rows)
    n_faltante = sum(1 for row in rows if row["missing_status"] == "FALTANTE")
    all_missing = [int(row["missing"]) for row in rows]
    total_missing = sum(all_missing)
    max_missing = max(all_missing)

    print()
    print("=== RESUMEN ===")
    print(f"  Frames totales:        {total}")
    print(f"  Frames FALTANTE:       {n_faltante} ({n_faltante/total:.0%})")
    print(f"  Frames OK (diag):      {total - n_faltante}")
    print(f"  Missing acumulado:     {total_missing}")
    print(f"  Missing maximo:        {max_missing}")
    if faltante_frames:
        top10 = sorted(faltante_frames, key=lambda x: x[1], reverse=True)[:10]
        print(f"  Top 10 por missing:")
        for name, m in top10:
            print(f"    {name}: {m}")
    print()
    print(f"  CSV:     {csv_path}")
    print(f"  Overlays: {overlay_dir}  ({n_faltante} imagenes)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Modo producción: inicia el sistema completo (PLC + cámaras + UI)."""
    from src.utils.logger import setup_logging
    from src.controller.system import InspectionSystem
    from src.ui.operator import launch_operator_ui as launch_production_ui

    setup_logging()

    disable_outputs = getattr(args, "no_plc_outputs", False)
    system = InspectionSystem(disable_plc_outputs=disable_outputs)
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

    sp = sub.add_parser(
        "missing-folder",
        help=(
            "Diagnóstico: detecta y exporta frames con agujeros faltantes. "
            "production_status usa grid_max_missing (no cambia). "
            "missing_status marca FALTANTE cuando missing >= --min-missing."
        ),
    )
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_1).")
    sp.add_argument("--input",   required=True, type=Path, help="Carpeta con frames a analizar.")
    sp.add_argument("--output",  required=True, type=Path, help="Carpeta de salida (CSV + overlays).")
    sp.add_argument("--min-missing", type=int, default=1, dest="min_missing",
                    help="Umbral de agujeros faltantes para marcar FALTANTE (default: 1).")
    sp.set_defaults(func=cmd_missing_folder)

    sp = sub.add_parser("run", help="Modo producción: PLC + cámaras + UI en tiempo real.")
    sp.add_argument("--no-plc-outputs", action="store_true", dest="no_plc_outputs",
                    help="Deshabilita escrituras al PLC (luces/solenoide/backlight). Solo analiza.")
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
