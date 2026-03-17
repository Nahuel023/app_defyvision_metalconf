import base64
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2

from src.inspection import (
    FolderInspectionSummary,
    InspectionResult,
    TemporalFrameResult,
    inspect_folder,
    inspect_image,
)
from src.patterns.pattern_build import build_pattern_from_image
from src.utils.config import DEFAULT_TOLERANCES, load_tolerances, save_tolerances


class DefyVisionApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("DEFYVISION Metalconf")
        self.root.geometry("1400x900")

        self.model_var = tk.StringVar(value=self._default_model())
        self.image_var = tk.StringVar()
        self.folder_var = tk.StringVar(value="data/frames")
        self.video_var = tk.StringVar()
        self.frames_dir_var = tk.StringVar(value="data/frames")
        self.fps_var = tk.StringVar(value="2")
        self.save_var = tk.BooleanVar(value=True)

        tolerances = load_tolerances()
        self.tolerance_vars = {
            key: tk.StringVar(value=str(tolerances.get(key, value)))
            for key, value in DEFAULT_TOLERANCES.items()
        }

        self.mask_photo: tk.PhotoImage | None = None
        self.overlay_photo: tk.PhotoImage | None = None
        self.folder_results: list[InspectionResult] = []
        self.temporal_results: list[TemporalFrameResult] = []
        self.selected_result_var = tk.StringVar(value="Sin frame seleccionado")
        self.summary_var = tk.StringVar(value="Todavia no hay analisis cargado.")
        self.response_var = tk.StringVar(value="Sin evaluacion temporal.")
        self.status_var = tk.StringVar(value="SIN DATOS")

        self._build_layout()
        self.refresh_models()

    def run(self) -> None:
        self.root.mainloop()

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        controls = ttk.Frame(self.root, padding=12)
        controls.grid(row=0, column=0, sticky="ns")

        preview = ttk.Frame(self.root, padding=12)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)
        preview.rowconfigure(2, weight=1)
        preview.rowconfigure(4, weight=1)
        preview.rowconfigure(6, weight=1)

        row = 0
        ttk.Label(controls, text="Modelo").grid(row=row, column=0, sticky="w")
        self.model_combo = ttk.Combobox(controls, textvariable=self.model_var, width=30)
        self.model_combo.grid(row=row, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(controls, text="Refrescar", command=self.refresh_models).grid(row=row, column=2, padx=(8, 0))

        row += 1
        ttk.Label(controls, text="Imagen").grid(row=row, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(controls, textvariable=self.image_var, width=36).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(12, 0))
        ttk.Button(controls, text="Buscar", command=self.choose_image).grid(row=row, column=2, padx=(8, 0), pady=(12, 0))

        row += 1
        ttk.Button(controls, text="Generar patron", command=self.on_build_pattern).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Button(controls, text="Analizar imagen", command=self.on_analyze_image).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Separator(controls, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)

        row += 1
        ttk.Label(controls, text="Carpeta frames").grid(row=row, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.folder_var, width=36).grid(row=row, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(controls, text="Buscar", command=self.choose_folder).grid(row=row, column=2, padx=(8, 0))

        row += 1
        ttk.Button(controls, text="Analizar carpeta", command=self.on_analyze_folder).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Separator(controls, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)

        row += 1
        ttk.Label(controls, text="Video").grid(row=row, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.video_var, width=36).grid(row=row, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(controls, text="Buscar", command=self.choose_video).grid(row=row, column=2, padx=(8, 0))

        row += 1
        ttk.Label(controls, text="Frames salida").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.frames_dir_var, width=36).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Button(controls, text="Buscar", command=self.choose_frames_dir).grid(row=row, column=2, padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(controls, text="FPS").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.fps_var, width=10).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Button(controls, text="Extraer frames", command=self.on_extract_frames).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Separator(controls, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)

        row += 1
        ttk.Label(controls, text="Tolerancias").grid(row=row, column=0, sticky="w")

        for key in (
            "threshold",
            "use_channel",
            "polarity",
            "min_area",
            "circularity_min",
            "tol_xy_px",
            "aspect_ratio_max",
            "align_match_tol_px",
            "min_match_count",
            "consecutive_nok_frames",
            "frame_rate_hz",
            "max_response_sec",
        ):
            row += 1
            ttk.Label(controls, text=key).grid(row=row, column=0, sticky="w")
            ttk.Entry(controls, textvariable=self.tolerance_vars[key], width=18).grid(row=row, column=1, sticky="w", padx=(8, 0))

        row += 1
        ttk.Checkbutton(controls, text="Guardar overlays", variable=self.save_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

        row += 1
        ttk.Button(controls, text="Guardar tolerancias", command=self.on_save_tolerances).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        controls.columnconfigure(1, weight=1)

        status_frame = ttk.Frame(preview)
        status_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        status_frame.columnconfigure(1, weight=1)

        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 24, "bold"),
            padx=20,
            pady=14,
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        summary_frame = ttk.Frame(status_frame)
        summary_frame.grid(row=0, column=1, sticky="ew", padx=(16, 0))
        summary_frame.columnconfigure(0, weight=1)
        ttk.Label(summary_frame, textvariable=self.summary_var, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(summary_frame, textvariable=self.response_var).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(summary_frame, textvariable=self.selected_result_var).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )

        ttk.Label(preview, text="Mask").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(preview, text="Overlay").grid(row=1, column=1, sticky="w", pady=(12, 0))

        self.mask_label = ttk.Label(preview)
        self.mask_label.grid(row=2, column=0, sticky="nsew", padx=(0, 8))

        self.overlay_label = ttk.Label(preview)
        self.overlay_label.grid(row=2, column=1, sticky="nsew")

        ttk.Label(preview, text="Log").grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.log_text = tk.Text(preview, height=14, wrap="word")
        self.log_text.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        nav_frame = ttk.Frame(preview)
        nav_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        nav_frame.columnconfigure(1, weight=1)

        ttk.Label(nav_frame, text="Resultados frame a frame").grid(row=0, column=0, sticky="w")
        ttk.Button(nav_frame, text="Anterior", command=self.show_previous_result).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(nav_frame, text="Siguiente", command=self.show_next_result).grid(row=0, column=3, padx=(8, 0))

        columns = ("frame", "raw", "temporal", "streak", "missing", "detected")
        self.results_tree = ttk.Treeview(preview, columns=columns, show="headings", height=10)
        self.results_tree.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.results_tree.heading("frame", text="Frame")
        self.results_tree.heading("raw", text="Raw")
        self.results_tree.heading("temporal", text="Temporal")
        self.results_tree.heading("streak", text="Racha NOK")
        self.results_tree.heading("missing", text="Missing")
        self.results_tree.heading("detected", text="Detected")
        self.results_tree.column("frame", width=220, anchor="w")
        self.results_tree.column("raw", width=70, anchor="center")
        self.results_tree.column("temporal", width=80, anchor="center")
        self.results_tree.column("streak", width=90, anchor="center")
        self.results_tree.column("missing", width=80, anchor="center")
        self.results_tree.column("detected", width=80, anchor="center")
        self.results_tree.tag_configure("ok_row", background="#e7f6ea")
        self.results_tree.tag_configure("nok_row", background="#fde8e7")
        self.results_tree.tag_configure("trigger_row", background="#f8b4b4")
        self.results_tree.bind("<<TreeviewSelect>>", self.on_select_result)

    def refresh_models(self) -> None:
        models_dir = Path("data/patterns")
        models = sorted(p.name for p in models_dir.iterdir() if p.is_dir()) if models_dir.exists() else []
        self.model_combo["values"] = models
        if models and self.model_var.get() not in models:
            self.model_var.set(models[0])

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleccionar imagen",
            filetypes=[("Imagenes", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff"), ("Todos", "*.*")],
        )
        if path:
            self.image_var.set(path)

    def choose_folder(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar carpeta de frames")
        if path:
            self.folder_var.set(path)

    def choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[("Videos", "*.mp4 *.mov *.avi *.mkv"), ("Todos", "*.*")],
        )
        if path:
            self.video_var.set(path)

    def choose_frames_dir(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar carpeta de salida para frames")
        if path:
            self.frames_dir_var.set(path)

    def on_save_tolerances(self) -> None:
        try:
            payload = {
                "threshold": int(self.tolerance_vars["threshold"].get()),
                "use_channel": self.tolerance_vars["use_channel"].get().strip(),
                "polarity": self.tolerance_vars["polarity"].get().strip(),
                "min_area": float(self.tolerance_vars["min_area"].get()),
                "circularity_min": float(self.tolerance_vars["circularity_min"].get()),
                "tol_xy_px": float(self.tolerance_vars["tol_xy_px"].get()),
                "aspect_ratio_max": float(self.tolerance_vars["aspect_ratio_max"].get()),
                "align_match_tol_px": float(self.tolerance_vars["align_match_tol_px"].get()),
                "min_match_count": int(self.tolerance_vars["min_match_count"].get()),
                "consecutive_nok_frames": int(self.tolerance_vars["consecutive_nok_frames"].get()),
                "frame_rate_hz": float(self.tolerance_vars["frame_rate_hz"].get()),
                "max_response_sec": float(self.tolerance_vars["max_response_sec"].get()),
            }
            save_tolerances(payload)
            self.log("Tolerancias guardadas en config/tolerancias.yaml")
        except Exception as exc:
            messagebox.showerror("Tolerancias", str(exc))

    def on_build_pattern(self) -> None:
        model = self.model_var.get().strip()
        img_path = self._path_from_var(self.image_var, "Selecciona una imagen primero.")
        if img_path is None:
            return
        try:
            self.on_save_tolerances()
            out = build_pattern_from_image(model=model, img_path=img_path)
            self.log(f"[build-pattern] model={model} saved={out}")
        except Exception as exc:
            messagebox.showerror("Generar patron", str(exc))

    def on_analyze_image(self) -> None:
        model = self.model_var.get().strip()
        img_path = self._path_from_var(self.image_var, "Selecciona una imagen primero.")
        if img_path is None:
            return
        try:
            self.on_save_tolerances()
            result = inspect_image(model, img_path, save=self.save_var.get())
            self.folder_results = [result]
            self.temporal_results = []
            self.populate_results_tree([])
            self.show_result(result)
            self.log_result(result)
            self.update_status_banner(raw_status=result.status, temporal_status=None)
            self.summary_var.set(
                f"Analisis individual: expected={result.report.expected} detected={result.report.detected} missing={result.report.missing}"
            )
            self.response_var.set("Sin evaluacion temporal para analisis individual.")
        except Exception as exc:
            messagebox.showerror("Analizar imagen", str(exc))

    def on_analyze_folder(self) -> None:
        model = self.model_var.get().strip()
        folder = self._path_from_var(self.folder_var, "Selecciona una carpeta primero.")
        if folder is None:
            return
        try:
            self.on_save_tolerances()
            summary = inspect_folder(model, folder, save=self.save_var.get())
            self.folder_results = summary.results
            self.temporal_results = summary.temporal_results
            self.populate_results_tree(summary.temporal_results)
            self.log_folder_summary(summary)
            self.update_folder_summary(summary)
            if summary.results:
                self.select_result_index(0)
        except Exception as exc:
            messagebox.showerror("Analizar carpeta", str(exc))

    def on_extract_frames(self) -> None:
        video_path = self._path_from_var(self.video_var, "Selecciona un video primero.")
        if video_path is None:
            return
        out_dir = Path(self.frames_dir_var.get().strip() or "data/frames")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            fps = float(self.fps_var.get())
        except ValueError:
            messagebox.showerror("Extraer frames", "FPS debe ser un numero.")
            return

        cmd = [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps}",
            str(out_dir / "frame_%04d.jpg"),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.folder_var.set(str(out_dir))
            self.log(f"[ffmpeg] Frames extraidos en {out_dir}")
        except FileNotFoundError:
            messagebox.showerror("Extraer frames", "No se encontro ffmpeg en PATH.")
        except subprocess.CalledProcessError as exc:
            messagebox.showerror("Extraer frames", exc.stderr[-1200:] or str(exc))

    def show_result(self, result: InspectionResult) -> None:
        self.mask_photo = self._to_photoimage(result.mask)
        self.overlay_photo = self._to_photoimage(result.overlay)
        self.mask_label.configure(image=self.mask_photo)
        self.overlay_label.configure(image=self.overlay_photo)

    def populate_results_tree(self, results: list[TemporalFrameResult]) -> None:
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        for idx, temporal in enumerate(results):
            result = temporal.result
            if temporal.triggered:
                tags = ("trigger_row",)
            elif temporal.decision_status == "NOK":
                tags = ("nok_row",)
            else:
                tags = ("ok_row",)
            self.results_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    result.image_path.name,
                    result.status,
                    temporal.decision_status,
                    temporal.nok_streak,
                    result.report.missing,
                    result.report.detected,
                ),
                tags=tags,
            )

    def on_select_result(self, _: object) -> None:
        selected = self.results_tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if 0 <= idx < len(self.folder_results):
            result = self.folder_results[idx]
            self.show_result(result)
            temporal = self.temporal_results[idx] if idx < len(self.temporal_results) else None
            self.log_result(result, temporal)
            self.update_selected_result(result, temporal)

    def select_result_index(self, idx: int) -> None:
        if not self.folder_results or not (0 <= idx < len(self.folder_results)):
            return
        item_id = str(idx)
        self.results_tree.selection_set(item_id)
        self.results_tree.focus(item_id)
        self.results_tree.see(item_id)
        self.show_result(self.folder_results[idx])
        temporal = self.temporal_results[idx] if idx < len(self.temporal_results) else None
        self.update_selected_result(self.folder_results[idx], temporal)

    def show_previous_result(self) -> None:
        if not self.folder_results:
            return
        current = self.results_tree.selection()
        idx = int(current[0]) if current else 0
        self.select_result_index(max(0, idx - 1))

    def show_next_result(self) -> None:
        if not self.folder_results:
            return
        current = self.results_tree.selection()
        idx = int(current[0]) if current else -1
        self.select_result_index(min(len(self.folder_results) - 1, idx + 1))

    def log_result(self, result: InspectionResult, temporal: TemporalFrameResult | None = None) -> None:
        if result.shift_xy is None:
            shift_txt = "skipped"
        else:
            shift_txt = f"dx={result.shift_xy[0]:.2f} dy={result.shift_xy[1]:.2f}"
        temporal_txt = ""
        if temporal is not None:
            temporal_txt = (
                f" temporal={temporal.decision_status} streak={temporal.nok_streak}"
            )
        self.log(
            f"[run-image] model={result.model} file={result.image_path.name} "
            f"expected={result.report.expected} detected={result.report.detected} "
            f"missing={result.report.missing} raw={result.status}{temporal_txt} "
            f"angle={result.angle_deg:.2f} lines={result.used_lines} shift={shift_txt}"
        )

    def log_folder_summary(self, summary: FolderInspectionSummary) -> None:
        self.log(
            f"[run-folder] model={summary.model} dir={summary.input_dir} "
            f"total={summary.total} raw_ok={summary.ok} raw_nok={summary.nok} "
            f"temporal_ok={summary.temporal_ok} temporal_nok={summary.temporal_nok}"
        )
        self.log(
            f"[temporal] consecutive_nok_frames={summary.consecutive_nok_frames} "
            f"fps={summary.frame_rate_hz:.2f} response_time_sec={summary.response_time_sec:.2f} "
            f"max_response_sec={summary.max_response_sec:.2f} "
            f"meets_target={summary.meets_response_target}"
        )
        for temporal in summary.temporal_results:
            self.log_result(temporal.result, temporal)

    def update_folder_summary(self, summary: FolderInspectionSummary) -> None:
        self.summary_var.set(
            f"Frames: {summary.total} | Raw OK: {summary.ok} | Raw NOK: {summary.nok} | Temporal NOK: {summary.temporal_nok}"
        )
        target_txt = "cumple" if summary.meets_response_target else "no cumple"
        self.response_var.set(
            f"Respuesta: {summary.response_time_sec:.2f}s con N={summary.consecutive_nok_frames} a {summary.frame_rate_hz:.2f} fps | Objetivo {summary.max_response_sec:.2f}s: {target_txt}"
        )
        if summary.temporal_nok > 0:
            self.update_status_banner(raw_status=None, temporal_status="NOK")
        else:
            self.update_status_banner(raw_status=None, temporal_status="OK")

    def update_selected_result(
        self,
        result: InspectionResult,
        temporal: TemporalFrameResult | None,
    ) -> None:
        if temporal is None:
            self.selected_result_var.set(
                f"Frame: {result.image_path.name} | Raw: {result.status} | Missing: {result.report.missing}"
            )
            self.update_status_banner(raw_status=result.status, temporal_status=None)
            return

        trigger_txt = " disparado" if temporal.triggered else ""
        self.selected_result_var.set(
            f"Frame: {result.image_path.name} | Raw: {result.status} | Temporal: {temporal.decision_status}{trigger_txt} | Racha NOK: {temporal.nok_streak} | Missing: {result.report.missing}"
        )
        self.update_status_banner(raw_status=result.status, temporal_status=temporal.decision_status)

    def update_status_banner(self, raw_status: str | None, temporal_status: str | None) -> None:
        if temporal_status == "NOK":
            text = "TEMPORAL NOK"
            bg = "#b91c1c"
        elif temporal_status == "OK":
            text = "TEMPORAL OK"
            bg = "#166534"
        elif raw_status == "NOK":
            text = "RAW NOK"
            bg = "#c2410c"
        elif raw_status == "OK":
            text = "RAW OK"
            bg = "#1d4ed8"
        else:
            text = "SIN DATOS"
            bg = "#3a3a3a"

        self.status_var.set(text)
        self.status_label.configure(bg=bg)

    def log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def _to_photoimage(self, image) -> tk.PhotoImage:
        preview = self._fit_image(image, max_width=560, max_height=360)
        ok, encoded = cv2.imencode(".png", preview)
        if not ok:
            raise RuntimeError("No se pudo convertir la imagen para preview.")
        return tk.PhotoImage(data=base64.b64encode(encoded.tobytes()).decode("ascii"))

    def _fit_image(self, image, max_width: int, max_height: int):
        h, w = image.shape[:2]
        scale = min(max_width / w, max_height / h, 1.0)
        if scale == 1.0:
            return image
        return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    def _path_from_var(self, var: tk.StringVar, message: str) -> Path | None:
        value = var.get().strip()
        if not value:
            messagebox.showwarning("Falta ruta", message)
            return None
        return Path(value)

    def _default_model(self) -> str:
        models_dir = Path("data/patterns")
        if not models_dir.exists():
            return ""
        models = sorted(p.name for p in models_dir.iterdir() if p.is_dir())
        return models[0] if models else ""


def launch_gui() -> None:
    DefyVisionApp().run()
