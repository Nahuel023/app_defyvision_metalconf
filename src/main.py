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


def cmd_detect_roi(args: argparse.Namespace) -> int:
    """Detecta automáticamente la ROI de la chapa a partir de imágenes con backlight."""
    import cv2
    import json

    from src.inspection import iter_image_files
    from src.patterns.roi import detect_roi_from_images, roi_path

    src: Path = args.img
    if not src.exists():
        print(f"[detect-roi] ERROR: no existe: {src}")
        return 1

    # Collect images: single file or folder (sample up to --max-frames)
    if src.is_dir():
        all_paths = list(iter_image_files(src))
        if not all_paths:
            print(f"[detect-roi] ERROR: no se encontraron imágenes en {src}")
            return 1
        step = max(1, len(all_paths) // args.max_frames)
        paths = all_paths[::step][: args.max_frames]
        print(f"[detect-roi] carpeta: {len(all_paths)} frames, usando {len(paths)}")
    else:
        paths = [src]
        print(f"[detect-roi] imagen única: {src.name}")

    imgs = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is not None:
            imgs.append(img)

    if not imgs:
        print("[detect-roi] ERROR: no se pudieron leer imágenes")
        return 1

    roi = detect_roi_from_images(
        imgs,
        channel=args.channel,
        margin_px=args.margin,
        min_contrast=args.min_contrast,
    )

    if roi is None:
        print("[detect-roi] ERROR: no se pudo detectar la ROI — verificar backlight y canal")
        return 1

    H, W = imgs[0].shape[:2]
    print(f"[detect-roi] frame: {W}x{H}")
    print(f"[detect-roi] ROI detectada: x={roi.x}  y={roi.y}  w={roi.w}  h={roi.h}")
    print(f"             izq={roi.x}px  der={roi.x + roi.w}px  margen={args.margin}px")

    # Build preview image: reference frame + ROI box + column profile overlay
    ref = imgs[len(imgs) // 2].copy()
    H_ref, W_ref = ref.shape[:2]
    # ROI rectangle
    cv2.rectangle(ref, (roi.x, 0), (roi.x + roi.w - 1, H_ref - 1), (0, 255, 255), 3)
    cv2.putText(ref, f"ROI x={roi.x} w={roi.w}", (max(0, roi.x + 4), 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
    # Column R-profile bar on top
    if args.channel == "r":
        ch_vis = ref[:, :, 2].astype(float)
    elif args.channel == "g":
        ch_vis = ref[:, :, 1].astype(float)
    elif args.channel == "b":
        ch_vis = ref[:, :, 0].astype(float)
    else:
        import cv2 as _cv2
        ch_vis = _cv2.cvtColor(ref, _cv2.COLOR_BGR2GRAY).astype(float)
    import numpy as _np
    col_p = _np.percentile(ch_vis, 20, axis=0)
    bar_h = 40
    for x in range(W_ref):
        val = int(col_p[x] / 255.0 * bar_h)
        cv2.line(ref, (x, bar_h - val), (x, bar_h), (180, 180, 180), 1)
    cv2.line(ref, (roi.x, 0), (roi.x, bar_h), (0, 255, 0), 2)
    cv2.line(ref, (roi.x + roi.w - 1, 0), (roi.x + roi.w - 1, bar_h), (0, 200, 255), 2)

    preview_path = Path("data/output/detect_roi_preview.png")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(preview_path), ref)
    print(f"[detect-roi] preview: {preview_path}")

    if args.show:
        _show_scaled_window("detect-roi — cualquier tecla para cerrar", ref)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    if args.dry_run:
        print("[detect-roi] --dry-run: ROI NO guardada")
        return 0

    out_path = roi_path(args.model, args.scanner)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"x": roi.x, "y": roi.y, "w": roi.w, "h": roi.h}),
        encoding="utf-8",
    )
    print(f"[detect-roi] guardado: {out_path}")
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
    from src.patterns.pattern_io import infer_scanner_id

    scanner_id = args.scanner or infer_scanner_id(args.model, args.img)
    result = inspect_image(args.model, args.img, save=args.save,
                           scanner_id=scanner_id)
    print(f"[context] scanner={scanner_id or '-'}")
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
    if result.roi_info is not None:
        info = result.roi_info
        print(
            f"[roi] frame={info.frame_w}x{info.frame_h} "
            f"saved_x={info.saved_roi.x} active_x={info.effective_roi.x} "
            f"shift_x={info.shift_x:+.1f} auto={info.auto_corrected}"
        )
        if info.warning:
            print(f"[roi] warn={info.warning}")

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
    from src.patterns.pattern_io import infer_scanner_id

    scanner_id = args.scanner or infer_scanner_id(args.model, args.input)

    summary = inspect_folder(
        args.model,
        args.input,
        save=args.save,
        frame_rate_hz=args.fps,
        scanner_id=scanner_id,
    )
    print(f"[context] scanner={scanner_id or '-'}")
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
    ms_count    = getattr(summary, "machine_stop_count", 0)
    print(
        f"[quality]  avg_detection_ratio={avg_ratio:.0%}  "
        f"align_failures={align_fails}/{summary.total}  "
        f"machine_stop_frames={ms_count}"
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
        if getattr(result, "machine_stop", False):
            warn += " MACHINE_STOP"
        print(
            f"  {result.image_path.name}: {result.status}/{temporal.decision_status}"
            f"  streak={temporal.nok_streak}"
            f"  missing={result.report.missing}  extra={result.report.extra}"
            f"  ratio={result.detection_ratio:.0%}{warn}"
        )
        if result.roi_info is not None and (result.roi_info.warning or result.roi_info.auto_corrected):
            print(
                f"    roi: frame={result.roi_info.frame_w}x{result.roi_info.frame_h} "
                f"active_x={result.roi_info.effective_roi.x} "
                f"shift_x={result.roi_info.shift_x:+.1f} "
                f"auto={result.roi_info.auto_corrected} "
                f"warn={result.roi_info.warning or '-'}"
            )

    # Summary of most frequently missing cells — helps identify edge artifacts vs real defects
    from collections import Counter
    cell_counts: Counter = Counter()
    for temporal in summary.temporal_results:
        for cell in (temporal.result.report.missing_cells or []):
            cell_counts[tuple(cell)] += 1
    if cell_counts:
        print(f"[missing-cells] celdas con mayor frecuencia de faltante (celda: N/{summary.total}):")
        for cell, count in cell_counts.most_common(10):
            pct = count / summary.total
            flag = " <-- borde" if pct > 0.05 else ""
            print(f"  celda {cell}: {count}/{summary.total}  ({pct:.0%}){flag}")

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


def cmd_center_folder(args: argparse.Namespace) -> int:
    """Diagnostic: measure sheet-centering for every frame in a folder.

    Exports:
      <output>/center_report.csv   — one row per frame, 20 columns
      <output>/center_overlays/    — annotated overlay PNG per frame

    This command does NOT touch PLC logic, production thresholds, or patterns.
    """
    import csv
    import cv2

    from src.inspection import inspect_image, iter_image_files

    input_dir: Path = args.input
    output_dir: Path = args.output
    model: str = args.model
    scanner: str | None = args.scanner

    if not input_dir.is_dir():
        print(f"[center-folder] ERROR: {input_dir} no es un directorio")
        return 1

    overlay_dir = output_dir / "center_overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    frames = list(iter_image_files(input_dir))
    if not frames:
        print("[center-folder] No se encontraron imagenes.")
        return 1

    print(f"[center-folder] model={model}  scanner={scanner or '(global)'}")
    print(f"[center-folder] frames={len(frames)}")
    print(f"[center-folder] salida -> {output_dir}")
    print()

    fieldnames = [
        "frame", "status", "missing",
        "centering_reliable",
        "sheet_left_x", "sheet_right_x", "sheet_width_px",
        "pattern_left_x", "pattern_right_x", "pattern_width_px",
        "left_margin_px", "right_margin_px", "margin_delta_px", "offset_px",
        "left_margin_std", "right_margin_std",
        "sheet_left_slope_deg", "sheet_right_slope_deg",
        "pattern_left_slope_deg", "pattern_right_slope_deg",
    ]

    rows: list[dict] = []

    for idx, img_path in enumerate(frames, 1):
        result = inspect_image(model, img_path, save=False, scanner_id=scanner)
        c = result.centering

        if c is not None:
            row = {
                "frame":                 img_path.name,
                "status":                result.status,
                "missing":               result.report.missing,
                "centering_reliable":    c.centering_reliable,
                "sheet_left_x":          f"{c.left_x:.1f}",
                "sheet_right_x":         f"{c.right_x:.1f}",
                "sheet_width_px":        f"{c.sheet_width_px:.1f}",
                "pattern_left_x":        f"{c.pattern_left_x:.1f}",
                "pattern_right_x":       f"{c.pattern_right_x:.1f}",
                "pattern_width_px":      f"{c.pattern_right_x - c.pattern_left_x:.1f}",
                "left_margin_px":        f"{c.left_margin_px:.1f}",
                "right_margin_px":       f"{c.right_margin_px:.1f}",
                "margin_delta_px":       f"{c.margin_delta_px:.1f}",
                "offset_px":             f"{c.offset_px:.2f}",
                "left_margin_std":       f"{c.left_margin_std:.2f}",
                "right_margin_std":      f"{c.right_margin_std:.2f}",
                "sheet_left_slope_deg":  f"{c.left_edge_slope_deg:.2f}",
                "sheet_right_slope_deg": f"{c.right_edge_slope_deg:.2f}",
                "pattern_left_slope_deg":  f"{c.pattern_left_slope_deg:.2f}",
                "pattern_right_slope_deg": f"{c.pattern_right_slope_deg:.2f}",
            }
        else:
            row = {
                "frame":   img_path.name,
                "status":  result.status,
                "missing": result.report.missing,
                **{k: "" for k in fieldnames[3:]},
            }

        rows.append(row)

        # Save centering overlay (main overlay already includes centering drawing)
        if result.overlay is not None:
            stem = img_path.stem
            cv2.imwrite(str(overlay_dir / f"{stem}_center.png"), result.overlay)

        if idx % 20 == 0 or idx == len(frames):
            n_reliable = sum(1 for r in rows if r.get("centering_reliable") == True)
            print(f"  {idx}/{len(frames)}  reliable={n_reliable}")

    # Write CSV
    csv_path = output_dir / "center_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    total = len(rows)
    valid_rows = [r for r in rows if r.get("centering_reliable") == True]
    n_reliable = len(valid_rows)

    offsets   = [float(r["offset_px"]) for r in valid_rows if r["offset_px"] != ""]
    lmargins  = [float(r["left_margin_px"]) for r in valid_rows if r["left_margin_px"] != ""]
    rmargins  = [float(r["right_margin_px"]) for r in valid_rows if r["right_margin_px"] != ""]

    def _median(lst):
        if not lst:
            return float("nan")
        s = sorted(lst)
        n = len(s)
        return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

    print()
    print("=== RESUMEN CENTRADO ===")
    print(f"  Frames totales:    {total}")
    print(f"  Mediciones fiables: {n_reliable} / {total}")
    if offsets:
        print(f"  Offset (px):  mediana={_median(offsets):+.2f}  min={min(offsets):+.2f}  max={max(offsets):+.2f}")
    if lmargins:
        print(f"  Margen Izq:   mediana={_median(lmargins):.1f}  min={min(lmargins):.1f}  max={max(lmargins):.1f}")
    if rmargins:
        print(f"  Margen Der:   mediana={_median(rmargins):.1f}  min={min(rmargins):.1f}  max={max(rmargins):.1f}")
    print()
    print(f"  CSV:      {csv_path}")
    print(f"  Overlays: {overlay_dir}  ({total} imagenes)")
    return 0


def cmd_roi_check(args: argparse.Namespace) -> int:
    """Diagnostic: verify frame size and ROI drift for one image or a folder."""
    import csv
    import statistics
    import cv2

    from src.inspection import iter_image_files
    from src.patterns.pattern_io import infer_scanner_id
    from src.patterns.roi import load_roi, resolve_runtime_roi

    src: Path = args.input
    scanner_id = args.scanner or infer_scanner_id(args.model, src)
    roi = load_roi(args.model, scanner_id)
    if roi is None:
        print(f"[roi-check] ERROR: no hay ROI guardada para model={args.model} scanner={scanner_id or '-'}")
        return 1
    if not src.exists():
        print(f"[roi-check] ERROR: no existe: {src}")
        return 1

    if src.is_dir():
        paths = list(iter_image_files(src))
        if not paths:
            print(f"[roi-check] ERROR: no se encontraron imagenes en {src}")
            return 1
    else:
        paths = [src]

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "roi_check.csv"

    rows = []
    shifts = []
    warned = 0
    size_counts: dict[tuple[int, int], int] = {}

    for path in paths:
        img = cv2.imread(str(path))
        if img is None:
            continue
        eff_roi, info = resolve_runtime_roi(
            img,
            roi,
            auto_correct_enabled=False,
            max_shift_px=args.max_shift,
            max_width_delta_px=args.max_width_delta,
            channel=args.channel,
            margin_px=args.margin,
            min_contrast=args.min_contrast,
        )
        size_counts[(info.frame_w, info.frame_h)] = size_counts.get((info.frame_w, info.frame_h), 0) + 1
        shifts.append(info.shift_x)
        if info.warning:
            warned += 1
        rows.append({
            "frame": path.name,
            "frame_w": info.frame_w,
            "frame_h": info.frame_h,
            "saved_x": roi.x,
            "saved_w": roi.w,
            "effective_x": eff_roi.x,
            "effective_w": eff_roi.w,
            "detected_x": "" if info.detected_roi is None else info.detected_roi.x,
            "detected_w": "" if info.detected_roi is None else info.detected_roi.w,
            "shift_x": f"{info.shift_x:.2f}",
            "width_delta_px": f"{info.width_delta_px:.2f}",
            "warning": info.warning,
        })

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "frame", "frame_w", "frame_h", "saved_x", "saved_w", "effective_x",
            "effective_w", "detected_x", "detected_w", "shift_x", "width_delta_px", "warning",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[roi-check] model={args.model}  scanner={scanner_id or '-'}  frames={len(rows)}")
    print(f"[roi-check] ROI guardada: x={roi.x} y={roi.y} w={roi.w} h={roi.h}")
    print(f"[roi-check] CSV: {csv_path}")
    if size_counts:
        print("[roi-check] tamanos detectados:")
        for (w, h), count in sorted(size_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {w}x{h}: {count}")
    if shifts:
        print(
            f"[roi-check] shift_x px: mediana={statistics.median(shifts):+.2f} "
            f"min={min(shifts):+.2f} max={max(shifts):+.2f}"
        )
    print(f"[roi-check] frames con advertencia: {warned}/{len(rows)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Modo producción: inicia el sistema completo (PLC + cámaras + UI)."""
    import sys

    from PyQt6.QtWidgets import QApplication

    from src.utils.logger import setup_logging
    from src.controller.system import InspectionSystem
    from src.ui.operator import ensure_license_or_prompt, launch_operator_ui as launch_production_ui

    setup_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    if not ensure_license_or_prompt(None):
        return 0

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
    from src.ui.login_dialog import LoginDialog, service_login_enabled
    from src.ui.service import ServiceWindow

    setup_logging()

    app = QApplication.instance() or QApplication(sys.argv)

    if service_login_enabled():
        dlg = LoginDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0

    system = InspectionSystem()
    system.connect_plc()
    system.start_cameras()

    win = ServiceWindow(system)
    win.showMaximized()
    app.exec()

    system.shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app_defyvision_metalconf",
        description="MVP CLI: inspeccion de patron de agujeros (OK/NOK).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "detect-roi",
        help="Detectar ROI automáticamente desde imágenes con backlight encendido.",
    )
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_1). Sin esto guarda ROI compartida.")
    sp.add_argument("--img",     required=True, type=Path,
                    help="Imagen de referencia o carpeta de frames con backlight encendido.")
    sp.add_argument("--channel", default="r", choices=["r", "g", "b", "gray"],
                    help="Canal para detectar transiciones backlight/chapa (default: r).")
    sp.add_argument("--margin",  type=int, default=0,
                    help="Píxeles a recortar hacia adentro desde el borde detectado (default: 0).")
    sp.add_argument("--min-contrast", type=float, default=30.0, dest="min_contrast",
                    help="Contraste mínimo entre backlight y chapa para confiar en la detección (default: 30).")
    sp.add_argument("--max-frames", type=int, default=20, dest="max_frames",
                    help="Máximo de frames a usar si se pasa una carpeta (default: 20).")
    sp.add_argument("--show",    action="store_true", help="Mostrar preview de la ROI sobre el frame.")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Mostrar ROI detectada sin guardar roi.json.")
    sp.set_defaults(func=cmd_detect_roi)

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

    sp = sub.add_parser(
        "center-folder",
        help=(
            "Diagnóstico: mide centrado del patrón respecto a bordes de chapa por frame. "
            "Exporta CSV (20 columnas) + overlays PNG. No toca lógica de producción."
        ),
    )
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_1).")
    sp.add_argument("--input",   required=True, type=Path, help="Carpeta con frames a analizar.")
    sp.add_argument("--output",  required=True, type=Path, help="Carpeta de salida (CSV + overlays).")
    sp.set_defaults(func=cmd_center_folder)

    sp = sub.add_parser(
        "roi-check",
        help=(
            "Diagnostico: verifica tamano de frame y deriva de ROI contra la ROI guardada. "
            "Exporta CSV; no toca la logica de produccion."
        ),
    )
    sp.add_argument("--model",   required=True, help="Nombre del modelo (ej: modelo_B).")
    sp.add_argument("--scanner", default=None,  help="ID del scanner (ej: scanner_2).")
    sp.add_argument("--input",   required=True, type=Path, help="Imagen o carpeta de frames.")
    sp.add_argument("--output",  required=True, type=Path, help="Carpeta de salida para CSV.")
    sp.add_argument("--channel", default="r", choices=["r", "g", "b", "gray"],
                    help="Canal para detectar bordes backlight/chapa (default: r).")
    sp.add_argument("--margin",  type=int, default=0,
                    help="Margen inward al detectar ROI (default: 0).")
    sp.add_argument("--min-contrast", type=float, default=30.0, dest="min_contrast",
                    help="Contraste minimo para confiar en la ROI detectada.")
    sp.add_argument("--max-shift", type=float, default=18.0, dest="max_shift",
                    help="Shift X maximo tolerado antes de advertir (default: 18).")
    sp.add_argument("--max-width-delta", type=float, default=20.0, dest="max_width_delta",
                    help="Delta de ancho maximo tolerado antes de advertir (default: 20).")
    sp.set_defaults(func=cmd_roi_check)

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
