"""
Ventana de ajuste de tolerancias de inspección por scanner.

Expone únicamente los parámetros seguros para el operario:
  - Cuántos agujeros faltantes para marcar NOK
  - Cuántos frames consecutivos antes de parada automática
  - Tolerancia de posición en píxeles
  - Umbral de inclinación de la chapa
  - Frames NOK consecutivos antes de FAULT
  - Segundos de evidencia a grabar

Los parámetros técnicos (geometría del patrón, umbrales de detección,
alineación, etc.) solo se modifican desde el modo servicio.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.utils.config import load_tolerances, save_scanner_overrides
from src.utils.model_names import to_display

from src.utils.paths import app_root
_ROOT = app_root()

# ----------------------------------------------------------------------
# Paleta (coherente con metrics_window.py / service.py)
# ----------------------------------------------------------------------
_DARK   = "#0f172a"
_PANEL  = "#1e293b"
_CARD   = "#243044"
_BORDER = "#334155"
_TEXT   = "#f1f5f9"
_MUTED  = "#94a3b8"
_ACCENT = "#38bdf8"
_OK     = "#4ade80"
_WARN   = "#fbbf24"
_NOK    = "#f87171"

# ----------------------------------------------------------------------
# Definición de parámetros expuestos al operario
# ----------------------------------------------------------------------
# Solo incluir params cuyo rango restringido NO puede romper la detección.
_PARAMS: list[dict[str, Any]] = [
    dict(
        key="frame_missing_nok_threshold",
        label="Agujeros faltantes para NOK",
        desc=(
            "Cuántos agujeros deben faltar en un frame para clasificarlo como NOK. "
            "Más bajo = más estricto."
        ),
        unit="agujeros",
        vtype="int",
        vmin=1, vmax=60, vstep=1,
    ),
    dict(
        key="machine_stop_missing_frames",
        label="Frames persistentes para parada",
        desc=(
            "Cuántos frames seguidos con el MISMO agujero faltante antes de detener "
            "la línea. Mínimo 2 (nunca para por un solo frame)."
        ),
        unit="frames",
        vtype="int",
        vmin=2, vmax=20, vstep=1,
    ),
    dict(
        key="consecutive_nok_frames",
        label="Frames NOK para FAULT",
        desc=(
            "Cuántos frames NOK seguidos antes de disparar FAULT y detener la línea. "
            "Valores grandes (p.ej. 9999) deshabilitan el FAULT automático."
        ),
        unit="frames",
        vtype="int",
        vmin=2, vmax=9999, vstep=1,
    ),
    dict(
        key="pattern_align_severe_abs_max_px",
        label="Sensibilidad ante chapa torcida",
        desc=(
            "Si la chapa se ve muy desviada o torcida en una sola foto, la máquina "
            "se detiene de inmediato, sin esperar varias fotos seguidas. "
            "Número MÁS BAJO = se detiene más fácil (más sensible). "
            "Número MÁS ALTO = solo se detiene ante desvíos más grandes."
        ),
        unit="px",
        vtype="float",
        vmin=5.0, vmax=60.0, vstep=1.0, decimals=1,
    ),
    dict(
        key="tol_xy_px",
        label="Tolerancia de posición",
        desc=(
            "Distancia máxima (en píxeles) entre donde la cámara ve un agujero y "
            "donde el patrón lo espera, para considerarlo como detectado. "
            "MÁS ALTO = más tolerante (útil si la cámara se movió levemente). "
            "MÁS BAJO = más estricto."
        ),
        unit="px",
        vtype="float",
        vmin=5.0, vmax=80.0, vstep=1.0, decimals=1,
    ),
    dict(
        key="compare_right_ignore_px",
        label="Ignorar borde DERECHO",
        desc=(
            "Píxeles del borde derecho de la imagen que se excluyen de la comparación. "
            "Si el programa no detecta agujeros del lado derecho, BAJAR este valor. "
            "0 = ningún agujero del borde derecho se ignora."
        ),
        unit="px",
        vtype="float",
        vmin=0.0, vmax=200.0, vstep=5.0, decimals=0,
    ),
    dict(
        key="compare_left_ignore_px",
        label="Ignorar borde IZQUIERDO",
        desc=(
            "Píxeles del borde izquierdo de la imagen que se excluyen de la comparación. "
            "Si el programa no detecta agujeros del lado izquierdo, BAJAR este valor. "
            "0 = ningún agujero del borde izquierdo se ignora."
        ),
        unit="px",
        vtype="float",
        vmin=0.0, vmax=200.0, vstep=5.0, decimals=0,
    ),
    dict(
        key="compare_top_ignore_px",
        label="Ignorar borde SUPERIOR",
        desc=(
            "Píxeles del borde superior de la imagen que se excluyen de la comparación. "
            "Si hay falsos faltantes en la parte de arriba, SUBIR este valor. "
            "0 = ningún agujero del borde superior se ignora."
        ),
        unit="px",
        vtype="float",
        vmin=0.0, vmax=200.0, vstep=5.0, decimals=0,
    ),
    dict(
        key="compare_bottom_ignore_px",
        label="Ignorar borde INFERIOR",
        desc=(
            "Píxeles del borde inferior de la imagen que se excluyen de la comparación. "
            "Si hay falsos faltantes en la parte de abajo, SUBIR este valor. "
            "0 = ningún agujero del borde inferior se ignora."
        ),
        unit="px",
        vtype="float",
        vmin=0.0, vmax=200.0, vstep=5.0, decimals=0,
    ),
]


# ----------------------------------------------------------------------
# Panel de ajuste de ROI (cámara en vivo, bordes izq/der, guardar)
# ----------------------------------------------------------------------

class _RoiPanel(QWidget):
    """Panel de ajuste de ROI integrado en la ventana de tolerancias.

    Arranca la cámara en vivo automáticamente al hacerse visible.
    El operario ajusta los bordes izquierdo y derecho con flechas y
    guarda la ROI; el patrón se reconstruye en segundo plano.
    """

    _BTN_SS = (
        f"QPushButton {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
        "border-radius:5px;font-size:11px;padding:0 12px;min-height:30px; }}"
        f"QPushButton:hover {{ border-color:{_ACCENT}; }}"
    )
    _BTN_LIVE_SS = (
        "QPushButton { background:#15803d;color:#ffffff;border:1px solid #16a34a;"
        "border-radius:5px;font-size:11px;padding:0 12px;min-height:30px; }"
        "QPushButton:hover { background:#166534; }"
    )
    _ARROW_SS = (
        f"QPushButton {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
        "border-radius:5px;font-size:16px;font-weight:700;"
        "min-width:38px;min-height:38px; }}"
        f"QPushButton:hover {{ border-color:{_ACCENT};color:{_ACCENT}; }}"
        "QPushButton:pressed { background:#0f172a; }"
    )
    _CHIP_SS = (
        f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
        f"background:{_DARK};border:1px solid {_BORDER};"
        "border-radius:4px;padding:2px 8px;background:transparent;border:none;"
    )

    def __init__(self, system: "InspectionSystem", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._system = system
        self._roi_frame: np.ndarray | None = None
        self._roi_lx: int = 0
        self._roi_rx: int = 0
        self._live_active: bool = False
        self._live_frame_count: int = 0
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(120)   # ~8 fps
        self._live_timer.timeout.connect(self._grab_live)
        self._rebuild_worker: QThread | None = None
        self._build_ui()

    # ── Construcción UI ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_PANEL};border-radius:8px;")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── Encabezado: título + selector de scanner ──────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        title_lbl = QLabel("AJUSTE DE ROI")
        title_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:13px;font-weight:700;letter-spacing:2px;"
            "background:transparent;"
        )
        top_row.addWidget(title_lbl)
        top_row.addStretch()

        sc_lbl = QLabel("Scanner:")
        sc_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;background:transparent;")
        top_row.addWidget(sc_lbl)

        self._scanner_combo = QComboBox()
        for sid in self._system.scanner_ids():
            num = sid.split("_")[-1]
            self._scanner_combo.addItem(f"Scanner {num}", userData=sid)
        self._scanner_combo.setFixedWidth(130)
        self._scanner_combo.setFixedHeight(30)
        self._scanner_combo.setStyleSheet(
            f"QComboBox {{ background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:11px;padding:0 8px; }"
            f"QComboBox::drop-down {{ border:none;width:20px; }}"
            f"QComboBox QAbstractItemView {{ background:{_CARD};color:{_TEXT}; }}"
        )
        self._scanner_combo.currentIndexChanged.connect(self._on_scanner_changed)
        top_row.addWidget(self._scanner_combo)
        root.addLayout(top_row)

        # ── ROI guardada ──────────────────────────────────────────────
        self._roi_saved_lbl = QLabel("ROI guardada: —")
        self._roi_saved_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-family:Consolas,monospace;background:transparent;"
        )
        root.addWidget(self._roi_saved_lbl)

        # ── Preview imagen en vivo ────────────────────────────────────
        self._preview_lbl = QLabel("Iniciando cámara…")
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumHeight(220)
        self._preview_lbl.setSizePolicy(
            self._preview_lbl.sizePolicy().horizontalPolicy(),
            __import__("PyQt6.QtWidgets", fromlist=["QSizePolicy"]).QSizePolicy.Policy.Expanding,
        )
        self._preview_lbl.setStyleSheet(
            f"background:#0a0f1a;border-radius:6px;border:1px solid {_BORDER};"
            f"color:{_MUTED};font-size:12px;"
        )
        root.addWidget(self._preview_lbl, stretch=1)

        # ── Controles de bordes ───────────────────────────────────────
        edges_row = QHBoxLayout()
        edges_row.setSpacing(14)

        # Borde izquierdo
        lx_chip = QLabel("BORDE IZQ")
        lx_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:4px;padding:2px 8px;"
        )
        edges_row.addWidget(lx_chip)
        self._btn_lx_left  = QPushButton("◄")
        self._btn_lx_right = QPushButton("►")
        self._btn_lx_left.setStyleSheet(self._ARROW_SS)
        self._btn_lx_right.setStyleSheet(self._ARROW_SS)
        self._btn_lx_left.clicked.connect(lambda: self._move("lx", -1))
        self._btn_lx_right.clicked.connect(lambda: self._move("lx", +1))
        edges_row.addWidget(self._btn_lx_left)
        edges_row.addWidget(self._btn_lx_right)
        self._lx_val_lbl = QLabel("x=—")
        self._lx_val_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-family:Consolas,monospace;background:transparent;"
        )
        edges_row.addWidget(self._lx_val_lbl)

        edges_row.addStretch()

        # Paso + ancho resultante
        paso_chip = QLabel("PASO")
        paso_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:4px;padding:2px 8px;"
        )
        edges_row.addWidget(paso_chip)
        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 100)
        self._step_spin.setValue(5)
        self._step_spin.setSuffix(" px")
        self._step_spin.setFixedWidth(78)
        self._step_spin.setFixedHeight(32)
        self._step_spin.setStyleSheet(
            f"QSpinBox {{ background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:2px 4px;font-size:12px;font-weight:700; }"
            f"QSpinBox::up-button,QSpinBox::down-button {{ width:20px;background:{_CARD};"
            f"border-left:1px solid {_BORDER}; }}"
        )
        edges_row.addWidget(self._step_spin)

        self._width_lbl = QLabel("w=—")
        self._width_lbl.setStyleSheet(
            f"color:{_ACCENT};font-size:12px;font-weight:700;"
            "font-family:Consolas,monospace;background:transparent;"
        )
        edges_row.addWidget(self._width_lbl)

        edges_row.addStretch()

        # Borde derecho
        rx_chip = QLabel("BORDE DER")
        rx_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:4px;padding:2px 8px;"
        )
        edges_row.addWidget(rx_chip)
        self._btn_rx_left  = QPushButton("◄")
        self._btn_rx_right = QPushButton("►")
        self._btn_rx_left.setStyleSheet(self._ARROW_SS)
        self._btn_rx_right.setStyleSheet(self._ARROW_SS)
        self._btn_rx_left.clicked.connect(lambda: self._move("rx", -1))
        self._btn_rx_right.clicked.connect(lambda: self._move("rx", +1))
        edges_row.addWidget(self._btn_rx_left)
        edges_row.addWidget(self._btn_rx_right)
        self._rx_val_lbl = QLabel("x=—")
        self._rx_val_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-family:Consolas,monospace;background:transparent;"
        )
        edges_row.addWidget(self._rx_val_lbl)

        root.addLayout(edges_row)

        # ── Guardar + live ────────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.setSpacing(12)

        self._live_btn = QPushButton("▶  Cámara en vivo")
        self._live_btn.setFixedHeight(36)
        self._live_btn.setCheckable(True)
        self._live_btn.setStyleSheet(self._BTN_SS)
        self._live_btn.clicked.connect(self._toggle_live)
        save_row.addWidget(self._live_btn)

        save_row.addStretch()

        self._save_btn = QPushButton("GUARDAR ROI")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setEnabled(False)
        self._save_btn.setMinimumWidth(180)
        self._save_btn.setStyleSheet(
            "QPushButton { background:#15803d;color:#ffffff;"
            "border-radius:8px;font-size:13px;font-weight:700;border:none; }"
            "QPushButton:hover { background:#166534; }"
            "QPushButton:disabled { background:#374151;color:#6b7280; }"
        )
        self._save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self._save_btn)
        root.addLayout(save_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas,monospace;background:transparent;"
        )
        root.addWidget(self._status_lbl)

        self._refresh_roi_label()

    # ── Helpers ───────────────────────────────────────────────────────

    def _current_sid(self) -> str:
        return self._scanner_combo.currentData() or ""

    def _model_for(self, sid: str) -> str:
        try:
            return self._system.io.scanner_config(sid).get("model", "")
        except Exception:
            return ""

    def _refresh_roi_label(self) -> None:
        from src.patterns.roi import load_roi
        sid   = self._current_sid()
        model = self._model_for(sid)
        roi   = load_roi(model, sid) if model else None
        if roi:
            self._roi_saved_lbl.setText(
                f"ROI guardada:  x={roi.x}  y={roi.y}  w={roi.w}  h={roi.h}"
            )
        else:
            self._roi_saved_lbl.setText("ROI guardada: — (sin ROI definida aún)")

    def _init_borders(self, sid: str, W: int) -> None:
        from src.patterns.roi import load_roi
        model = self._model_for(sid)
        roi   = load_roi(model, sid) if model else None
        if roi:
            self._roi_lx = roi.x
            self._roi_rx = roi.x + roi.w
        else:
            self._roi_lx = 0
            self._roi_rx = W

    # ── Cámara en vivo ────────────────────────────────────────────────

    def start_live(self) -> None:
        sid = self._current_sid()
        try:
            self._system.camera(sid)
        except Exception:
            self._status_lbl.setText("No hay cámara disponible para este scanner.")
            return
        self._live_active = True
        self._live_frame_count = 0
        self._live_btn.setText("⏹  Detener live")
        self._live_btn.setChecked(True)
        self._live_btn.setStyleSheet(self._BTN_LIVE_SS)
        self._status_lbl.setText(f"Cámara en vivo: {sid}")
        self._live_timer.start()

    def stop_live(self) -> None:
        self._live_active = False
        self._live_timer.stop()
        self._live_btn.setText("▶  Cámara en vivo")
        self._live_btn.setChecked(False)
        self._live_btn.setStyleSheet(self._BTN_SS)
        if self._roi_frame is not None:
            self._status_lbl.setText("Live detenido — frame congelado.")
        else:
            self._status_lbl.setText("")

    def _toggle_live(self) -> None:
        if self._live_active:
            self.stop_live()
        else:
            self.start_live()

    def _grab_live(self) -> None:
        if not self._live_active:
            return
        sid = self._current_sid()
        try:
            cam   = self._system.camera(sid)
            frame = cam.get_frame()
        except Exception:
            frame = None
        if frame is None:
            return
        if self._roi_frame is None:
            self._init_borders(sid, frame.shape[1])
            self._save_btn.setEnabled(True)
        self._roi_frame = frame
        self._redraw()

    def _on_scanner_changed(self) -> None:
        self._roi_frame = None
        self._refresh_roi_label()
        if self._live_active:
            # Reiniciar captura para el nuevo scanner
            self._live_timer.stop()
            self._live_frame_count = 0
            self._live_timer.start()

    # ── Dibujo del preview ────────────────────────────────────────────

    def _move(self, edge: str, direction: int) -> None:
        if self._roi_frame is None:
            return
        step = self._step_spin.value() * direction
        W = self._roi_frame.shape[1]
        if edge == "lx":
            self._roi_lx = max(0, min(self._roi_rx - 1, self._roi_lx + step))
        else:
            self._roi_rx = max(self._roi_lx + 1, min(W, self._roi_rx + step))
        self._redraw()

    def _redraw(self) -> None:
        if self._roi_frame is None:
            return
        lx, rx = self._roi_lx, self._roi_rx
        H, W   = self._roi_frame.shape[:2]

        self._lx_val_lbl.setText(f"x={lx}")
        self._rx_val_lbl.setText(f"x={rx}")
        self._width_lbl.setText(f"w={rx - lx}")

        vis = self._roi_frame.copy()
        vis[:, :lx] = (vis[:, :lx] * 0.3).astype("uint8")
        vis[:, rx:] = (vis[:, rx:] * 0.3).astype("uint8")
        cv2.line(vis, (lx, 0), (lx, H - 1), (0, 255, 100), 2)
        cv2.line(vis, (rx, 0), (rx, H - 1), (0, 200, 255), 2)
        cv2.rectangle(vis, (lx, 2), (rx, H - 3), (255, 255, 255), 1)

        pw    = max(self._preview_lbl.width(), 300)
        scale = pw / W
        thumb = cv2.resize(vis, (pw, int(H * scale)), interpolation=cv2.INTER_AREA)
        th, tw = thumb.shape[:2]
        rgb   = np.ascontiguousarray(cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB))
        qimg  = QImage(rgb.data, tw, th, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        pix   = QPixmap.fromImage(qimg)
        self._preview_lbl.setPixmap(
            pix.scaled(
                self._preview_lbl.width(),
                self._preview_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ── Guardar ROI ───────────────────────────────────────────────────

    def _on_save(self) -> None:
        if self._roi_frame is None:
            return
        lx, rx = self._roi_lx, self._roi_rx
        if rx <= lx:
            return
        H   = self._roi_frame.shape[0]
        sid = self._current_sid()
        model = self._model_for(sid)
        if not model:
            QMessageBox.warning(self, "Sin modelo", "No hay modelo configurado para este scanner.")
            return

        from src.patterns.roi import roi_path
        path = roi_path(model, sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"x": lx, "y": 0, "w": rx - lx, "h": H}, indent=2),
            encoding="utf-8",
        )
        self._save_btn.setEnabled(False)
        self._status_lbl.setText("ROI guardada — reconstruyendo patrón…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:10px;")
        self._refresh_roi_label()
        self._rebuild_pattern_async(self._roi_frame.copy(), model, sid)

    def _rebuild_pattern_async(self, frame: np.ndarray, model: str, scanner_id: str) -> None:
        class _Worker(QThread):
            done = pyqtSignal(str, bool)

            def __init__(self, frame: np.ndarray, model: str, scanner_id: str) -> None:
                super().__init__()
                self._frame      = frame
                self._model      = model
                self._scanner_id = scanner_id

            def run(self) -> None:
                tmp = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                        tmp = f.name
                    cv2.imwrite(tmp, self._frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    from src.patterns.pattern_build import build_pattern_from_image
                    out = build_pattern_from_image(
                        self._model, Path(tmp), scanner_id=self._scanner_id
                    )
                    self.done.emit(f"ROI y patron guardados → {out.name}", True)
                except Exception as exc:
                    self.done.emit(f"ROI guardada  ·  Error reconstruyendo patron: {exc}", False)
                finally:
                    if tmp and os.path.exists(tmp):
                        os.unlink(tmp)

        _sid = scanner_id
        worker = _Worker(frame, model, scanner_id)

        def _on_done(msg: str, ok: bool) -> None:
            color = "#22c55e" if ok else "#f87171"
            if ok and _sid:
                try:
                    self._system.scanner(_sid).reload_cache()
                    msg += " · aplicado al análisis en vivo"
                except Exception:
                    pass
            self._status_lbl.setText(msg)
            self._status_lbl.setStyleSheet(f"color:{color};font-size:10px;")
            self._save_btn.setEnabled(True)
            self._rebuild_worker = None

        worker.done.connect(_on_done)
        worker.done.connect(lambda *_: worker.deleteLater())
        self._rebuild_worker = worker
        worker.start()


# ----------------------------------------------------------------------
# Panel de un scanner
# ----------------------------------------------------------------------

class _ScannerTolerancePanel(QWidget):
    def __init__(
        self,
        scanner_id: str,
        system: InspectionSystem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = scanner_id
        self._system = system
        self._spinboxes: dict[str, QSpinBox | QDoubleSpinBox] = {}
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------

    def _model(self) -> str:
        return self._system.io.scanner_config(self._id).get("model", "")

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_PANEL};")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # ── Título: scanner + tipo de placa ───────────────────────────
        _num = self._id.split("_")[-1]
        self._model_lbl = QLabel(f"Scanner {_num}  ·  —")
        self._model_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._model_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:14px;font-weight:700;"
            f"letter-spacing:1px;background:transparent;"
        )
        root.addWidget(self._model_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_BORDER};")
        root.addWidget(sep)

        # ── Parámetros ────────────────────────────────────────────────
        for p in _PARAMS:
            root.addWidget(self._param_card(p))

        root.addStretch()

        # ── Botón guardar ─────────────────────────────────────────────
        self._save_btn = QPushButton(f"Guardar {self._id.replace('_',' ').upper()}")
        self._save_btn.setMinimumHeight(44)
        self._save_btn.setStyleSheet(
            f"background:{_ACCENT};color:#0f172a;font-weight:700;"
            "border-radius:8px;font-size:13px;border:none;"
        )
        self._save_btn.clicked.connect(self._on_save)
        root.addWidget(self._save_btn)

    def _param_card(self, p: dict) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_CARD};border-radius:8px;"
            f"border:1px solid {_BORDER};}}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        # Fila superior: label + spinbox + unidad
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        lbl = QLabel(p["label"])
        lbl.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:600;"
            "background:transparent;border:none;"
        )
        top_row.addWidget(lbl, stretch=1)

        if p["vtype"] == "int":
            sb: QSpinBox | QDoubleSpinBox = QSpinBox()
            sb.setRange(p["vmin"], p["vmax"])
            sb.setSingleStep(p["vstep"])
        else:
            sb = QDoubleSpinBox()
            sb.setRange(p["vmin"], p["vmax"])
            sb.setSingleStep(p["vstep"])
            sb.setDecimals(p.get("decimals", 1))

        sb.setFixedWidth(110)
        sb.setFixedHeight(36)
        sb.setStyleSheet(
            f"QSpinBox, QDoubleSpinBox {{"
            f"  background:#0f172a; color:{_TEXT};"
            f"  border:1px solid {_BORDER}; border-radius:4px;"
            f"  font-size:14px; font-weight:700; padding:2px 4px;"
            f"}}"
            f"QSpinBox::up-button, QDoubleSpinBox::up-button {{"
            f"  width:22px; border-left:1px solid {_BORDER};"
            f"  background:#243044; border-top-right-radius:4px;"
            f"}}"
            f"QSpinBox::down-button, QDoubleSpinBox::down-button {{"
            f"  width:22px; border-left:1px solid {_BORDER};"
            f"  background:#243044; border-bottom-right-radius:4px;"
            f"}}"
            f"QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            f"QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{"
            f"  background:#38bdf8;"
            f"}}"
            f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{"
            f"  width:8px; height:8px;"
            f"}}"
        )
        top_row.addWidget(sb)

        unit_lbl = QLabel(p["unit"])
        unit_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;background:transparent;border:none;"
        )
        unit_lbl.setFixedWidth(44)
        top_row.addWidget(unit_lbl)

        lay.addLayout(top_row)

        # Descripción
        desc = QLabel(p["desc"])
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color:{_MUTED};font-size:10px;background:transparent;border:none;"
        )
        lay.addWidget(desc)

        self._spinboxes[p["key"]] = sb
        return card

    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        model = self._model()
        _num = self._id.split("_")[-1]
        display = to_display(model) if model else "—"
        self._model_lbl.setText(f"Scanner {_num}  ·  {display}")
        if not model:
            return
        tols = load_tolerances(model, scanner_id=self._id)
        for p in _PARAMS:
            sb = self._spinboxes[p["key"]]
            raw = tols.get(p["key"])
            if raw is None:
                continue
            if isinstance(sb, QDoubleSpinBox):
                sb.setValue(float(raw))
            else:
                sb.setValue(int(raw))

    def _on_save(self) -> None:
        model = self._model()
        if not model:
            QMessageBox.warning(self, "Sin modelo", f"No hay modelo configurado para {self._id}.")
            return

        updates: dict[str, Any] = {}
        for p in _PARAMS:
            sb = self._spinboxes[p["key"]]
            updates[p["key"]] = sb.value()

        # Confirmación si consecutive_nok_frames está en valor de calibración
        nok_frames = updates.get("consecutive_nok_frames", 0)
        if isinstance(nok_frames, (int, float)) and int(nok_frames) >= 500:
            resp = QMessageBox.question(
                self,
                "FAULT desactivado",
                f"consecutive_nok_frames = {int(nok_frames)}\n\n"
                "Con este valor el FAULT automático está prácticamente desactivado.\n"
                "¿Guardar de todas formas?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        try:
            save_scanner_overrides(self._id, updates)
            # Fuerza recarga de tolerancias en el scanner activo
            scanner = self._system.scanner(self._id)
            scanner.set_model(model)
            QMessageBox.information(
                self,
                "Guardado",
                f"Tolerancias de {to_display(model)} guardadas correctamente.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"No se pudo guardar:\n{exc}")


# ----------------------------------------------------------------------
# Ventana principal con dos pestañas: Tolerancias | Ajuste de ROI
# ----------------------------------------------------------------------

class ToleranceWindow(QMainWindow):
    def __init__(
        self, system: InspectionSystem, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._system = system
        self._roi_panel: _RoiPanel | None = None
        self._stack: QStackedWidget | None = None
        self.setWindowTitle("Tolerancias — DEFYVISION")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.setStyleSheet(f"background:{_DARK};")
        self.resize(1000, 820)
        self._build_ui()

    # ── Construcción ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background:{_DARK};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ── Barra de encabezado: título + tabs ────────────────────────
        root.addWidget(self._build_header())

        # ── Stacked widget ────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
        self._stack.addWidget(self._build_params_page())   # índice 0
        self._roi_panel = _RoiPanel(self._system)
        self._stack.addWidget(self._roi_panel)              # índice 1
        root.addWidget(self._stack, stretch=1)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"background:{_PANEL};border-radius:8px;border:1px solid {_BORDER};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        title = QLabel("TOLERANCIAS")
        title.setStyleSheet(
            f"color:{_TEXT};font-size:18px;font-weight:700;"
            "letter-spacing:4px;background:transparent;border:none;"
        )
        lay.addWidget(title)
        lay.addStretch()

        def _tab(label: str, idx: int, active_color: str) -> QPushButton:
            btn = QPushButton(label)
            btn.setFixedHeight(34)
            btn.setMinimumWidth(160)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{_MUTED};"
                f"border:1px solid {_BORDER}; border-radius:7px;"
                f"font-size:12px; font-weight:700; padding:0 16px; }}"
                f"QPushButton:checked {{ background:{active_color};"
                f"color:#ffffff; border-color:{active_color}; }}"
                f"QPushButton:hover:!checked {{ background:{active_color}22;"
                f"border-color:{active_color}; color:{_TEXT}; }}"
            )
            btn.clicked.connect(lambda: self._switch_tab(idx))
            return btn

        self._tab_tol = _tab("Tolerancias",   0, "#065f46")
        self._tab_roi = _tab("Ajuste de ROI", 1, "#0369a1")
        self._tab_tol.setChecked(True)

        lay.addWidget(self._tab_tol)
        lay.addWidget(self._tab_roi)
        return bar

    def _build_params_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_DARK};")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        # ── Aviso ─────────────────────────────────────────────────────
        warn = QLabel(
            "⚠   Solo modificar si el análisis tiene demasiados falsos errores "
            "o no detecta eficientemente defectos reales."
        )
        warn.setWordWrap(True)
        warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warn.setStyleSheet(
            "color:#78350f;font-size:11px;font-weight:600;"
            "background:#fef3c7;border:1px solid #fbbf24;"
            "border-radius:6px;padding:8px 14px;"
        )
        root.addWidget(warn)

        # ── Paneles por scanner ───────────────────────────────────────
        panels_row = QHBoxLayout()
        panels_row.setSpacing(12)
        for sid in self._system.scanner_ids():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet(
                f"QScrollArea{{border:none;background:{_PANEL};}}"
                "QScrollBar:vertical{width:6px;background:#1e293b;}"
                "QScrollBar::handle:vertical{background:#334155;border-radius:3px;}"
            )
            panel = _ScannerTolerancePanel(sid, self._system)
            scroll.setWidget(panel)
            panels_row.addWidget(scroll)
        root.addLayout(panels_row, stretch=1)

        # ── Botón recargar ────────────────────────────────────────────
        reload_btn = QPushButton("Recargar valores actuales")
        reload_btn.setFixedHeight(32)
        reload_btn.setStyleSheet(
            f"background:transparent;color:{_MUTED};"
            f"border:1px solid {_BORDER};border-radius:6px;font-size:11px;"
        )
        reload_btn.clicked.connect(self._reload_all)
        root.addWidget(reload_btn)
        return page

    # ── Navegación de pestañas ────────────────────────────────────────

    def _switch_tab(self, idx: int) -> None:
        prev = self._stack.currentIndex() if self._stack else 0
        self._stack.setCurrentIndex(idx)
        self._tab_tol.setChecked(idx == 0)
        self._tab_roi.setChecked(idx == 1)
        # Gestionar cámara en vivo
        if idx == 1 and prev != 1 and self._roi_panel is not None:
            self._roi_panel.start_live()
        elif idx != 1 and prev == 1 and self._roi_panel is not None:
            self._roi_panel.stop_live()

    # ── Eventos de ventana ────────────────────────────────────────────

    def _reload_all(self) -> None:
        central = self.centralWidget()
        for scroll in central.findChildren(QScrollArea):
            panel = scroll.widget()
            if isinstance(panel, _ScannerTolerancePanel):
                panel._load_values()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Solo arrancar live si la pestaña ROI está activa al abrir
        if (self._roi_panel is not None
                and self._stack is not None
                and self._stack.currentIndex() == 1):
            self._roi_panel.start_live()

    def closeEvent(self, event) -> None:
        if self._roi_panel is not None:
            self._roi_panel.stop_live()
        super().closeEvent(event)
