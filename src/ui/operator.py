"""
Interfaz de operador de producción (PyQt6).

Muestra ambos scanners en paralelo con:
  - Feed de cámara en vivo (~30 fps)
  - Estado del sistema (IDLE / RUNNING / FAULT / ERROR)
  - Modo de operación (MANUAL / AUTO)
  - Racha de NOK y último resultado
  - Controles: INICIAR / DETENER / RESET
  - Selector de modelo por scanner
  - Log de eventos independiente por scanner
  - Área de branding (logos reemplazables)
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.inspection import InspectionResult
from src.utils.state import OperationMode, ScannerState

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
_COLOR = {
    ScannerState.IDLE:    ("#475569", "#ffffff"),
    ScannerState.RUNNING: ("#166534", "#ffffff"),
    ScannerState.FAULT:   ("#b91c1c", "#ffffff"),
    ScannerState.ERROR:   ("#92400e", "#ffffff"),
}
_MODE_COLOR = {
    OperationMode.AUTO:   "#1d4ed8",
    OperationMode.MANUAL: "#374151",
}
_CAMERA_REFRESH_MS = 33     # ~30 fps
_STATUS_REFRESH_MS = 200
_OVERLAY_HOLD_MS   = 2500   # ms que se muestra el overlay post-inspección


# ------------------------------------------------------------------
# Panel de un scanner
# ------------------------------------------------------------------

class ScannerPanel(QWidget):
    """Panel completo para un scanner (cámara + estado + controles + log propio)."""

    def __init__(self, scanner_id: str, system: InspectionSystem,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._id = scanner_id
        self._system = system
        self._scanner = system.scanner(scanner_id)
        self._camera = system.camera(scanner_id)

        self._last_overlay: Optional[np.ndarray] = None
        self._overlay_until_ms: int = 0

        self._build_ui()
        self._populate_models()

        self._scanner.on_state_changed = self._on_state_changed
        self._scanner.on_result = self._on_result

    # ------------------------------------------------------------------
    # Construcción de UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Título
        title = QLabel(self._id.replace("_", " ").upper())
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size:15px;font-weight:700;color:#1e293b;"
            "background:#e2e8f0;border-radius:6px;padding:5px;"
        )
        root.addWidget(title)

        # Feed de cámara
        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setMinimumSize(440, 300)
        self.camera_label.setStyleSheet("background:#0f172a;border-radius:8px;")
        self.camera_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.camera_label, stretch=1)

        # Badge de estado
        self.state_badge = QLabel("● IDLE")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_badge.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        bg, fg = _COLOR[ScannerState.IDLE]
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;padding:7px 14px;"
        )
        root.addWidget(self.state_badge)

        # Métricas
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(6)
        self._mode_val   = self._metric_card("Modo",      "MANUAL", _MODE_COLOR[OperationMode.MANUAL])
        self._streak_val = self._metric_card("Racha NOK", "0",      "#374151")
        self._result_val = self._metric_card("Último",    "—",      "#374151")
        for w in (self._mode_val[0], self._streak_val[0], self._result_val[0]):
            metrics_row.addWidget(w)
        root.addLayout(metrics_row)

        # Selector de modelo
        model_row = QHBoxLayout()
        lbl = QLabel("Modelo:")
        lbl.setStyleSheet("font-size:12px;color:#374151;")
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet("font-size:12px;")
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(lbl)
        model_row.addWidget(self.model_combo, stretch=1)
        root.addLayout(model_row)

        # Botones de control
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.start_btn = self._control_btn("▶  INICIAR", "#166534")
        self.stop_btn  = self._control_btn("■  DETENER", "#b91c1c")
        self.reset_btn = self._control_btn("↺  RESET",   "#1d4ed8")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.reset_btn)
        root.addLayout(btn_row)

        # Log propio del scanner
        log_header = QLabel(f"Log — {self._id.replace('_', ' ').upper()}")
        log_header.setStyleSheet("font-size:10px;color:#64748b;font-weight:600;margin-top:4px;")
        root.addWidget(log_header)

        self._log_widget = QTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMaximumHeight(100)
        self._log_widget.setFont(QFont("Consolas", 8))
        self._log_widget.setStyleSheet(
            "background:#0f172a;color:#94a3b8;border:none;border-radius:6px;padding:4px;"
        )
        root.addWidget(self._log_widget)

        self._refresh_buttons(ScannerState.IDLE)

    def _metric_card(self, title: str, value: str, color: str) -> tuple[QWidget, QLabel]:
        w = QWidget()
        w.setStyleSheet("background:#f8fafc;border-radius:7px;border:1px solid #e2e8f0;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet("font-size:9px;color:#94a3b8;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v = QLabel(value)
        v.setStyleSheet(f"font-size:13px;font-weight:700;color:{color};")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(v)
        return w, v

    def _control_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(34)
        btn.setStyleSheet(
            f"background:{color};color:white;font-weight:700;"
            "border-radius:6px;font-size:12px;"
            f"border:none;"
            f"padding:0 8px;"
        )
        return btn

    # ------------------------------------------------------------------
    # Refresco (llamado por timers del padre)
    # ------------------------------------------------------------------

    def refresh_camera(self) -> None:
        now_ms = int(time.monotonic() * 1000)
        if self._last_overlay is not None and now_ms < self._overlay_until_ms:
            frame = self._last_overlay
        else:
            frame = self._camera.get_frame()
            if frame is None:
                return

        rect = self.camera_label.contentsRect()
        w = max(440, rect.width() - 4)
        h = max(300, rect.height() - 4)
        self.camera_label.setPixmap(_bgr_to_pixmap(frame, w, h))

    def refresh_status(self) -> None:
        s = self._scanner.get_status()
        state: ScannerState        = s["state"]
        mode: OperationMode        = s["mode"]
        streak: int                = s["nok_streak"]
        result: Optional[InspectionResult] = s["last_result"]

        # Badge estado
        bg, fg = _COLOR[state]
        self.state_badge.setText(f"● {state.value.upper()}")
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;"
            "padding:7px 14px;font-size:13px;font-weight:700;"
        )

        # Modo
        mc = _MODE_COLOR[mode]
        self._mode_val[1].setText(mode.value.upper())
        self._mode_val[1].setStyleSheet(f"font-size:13px;font-weight:700;color:{mc};")

        # Racha NOK
        sc = "#b91c1c" if streak > 0 else "#374151"
        self._streak_val[1].setText(str(streak))
        self._streak_val[1].setStyleSheet(f"font-size:13px;font-weight:700;color:{sc};")

        # Último resultado
        if result:
            rc = "#166534" if result.status == "OK" else "#b91c1c"
            self._result_val[1].setText(f"{result.status} ({result.report.missing} falt.)")
            self._result_val[1].setStyleSheet(f"font-size:13px;font-weight:700;color:{rc};")

        self._refresh_buttons(state)

    # ------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if not self._scanner.start():
            QMessageBox.warning(self, "Iniciar", f"No se pudo iniciar {self._id}.")
        else:
            self._log("INICIADO")

    def _on_stop(self) -> None:
        self._scanner.stop()
        self._log("DETENIDO")

    def _on_reset(self) -> None:
        if self._scanner.reset():
            self._log("RESET — reanudando inspección")
        else:
            QMessageBox.information(self, "Reset", "Solo disponible en estado FAULT.")

    def _on_model_changed(self, model: str) -> None:
        if model:
            self._scanner.set_model(model)
            self._log(f"Modelo → {model}")

    # ------------------------------------------------------------------
    # Callbacks del controlador (desde threads)
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: ScannerState, mode: OperationMode) -> None:
        self._log(f"Estado → {state.value.upper()} / {mode.value.upper()}")

    def _on_result(self, result: InspectionResult, streak: int) -> None:
        self._last_overlay = result.overlay.copy()
        self._overlay_until_ms = int(time.monotonic() * 1000) + _OVERLAY_HOLD_MS
        label = "OK" if result.status == "OK" else f"NOK — {result.report.missing} faltante(s)"
        self._log(f"{label}  |  racha={streak}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_buttons(self, state: ScannerState) -> None:
        self.start_btn.setEnabled(state == ScannerState.IDLE)
        self.stop_btn.setEnabled(state != ScannerState.IDLE)
        self.reset_btn.setEnabled(state == ScannerState.FAULT)

    def _populate_models(self) -> None:
        patterns_dir = Path("data/patterns")
        models = sorted(p.name for p in patterns_dir.iterdir() if p.is_dir()) \
            if patterns_dir.exists() else []
        current = self._system.io.scanner_config(self._id).get("model", "")
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current in models:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_widget.append(f"[{ts}] {msg}")


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

class OperatorWindow(QMainWindow):
    def __init__(self, system: InspectionSystem) -> None:
        super().__init__()
        self._system = system
        self.setWindowTitle("DEFYVISION — Metalconf")
        self.resize(1400, 860)
        self._build_ui()

        self._camera_timer = QTimer(self)
        self._camera_timer.timeout.connect(self._refresh_cameras)
        self._camera_timer.start(_CAMERA_REFRESH_MS)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(_STATUS_REFRESH_MS)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._panels: dict[str, ScannerPanel] = {}

        for sid in self._system.scanner_ids():
            panel = ScannerPanel(sid, self._system)
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.addWidget(panel)
            splitter.addWidget(frame)
            self._panels[sid] = panel

        for i in range(splitter.count()):
            splitter.setStretchFactor(i, 1)

        root.addWidget(splitter, stretch=1)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(68)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0f172a, stop:1 #1e3a5f);"
            "border-radius:10px;"
        )

        outer = QHBoxLayout(header)
        outer.setContentsMargins(22, 0, 22, 0)
        outer.setSpacing(16)

        # ── Logo izquierdo ──────────────────────────────────────────
        logo_left = QLabel("DEFYMOTION")
        logo_left.setStyleSheet(
            "color:#38bdf8;font-size:17px;font-weight:700;"
            "letter-spacing:2px;background:transparent;"
        )
        outer.addWidget(logo_left)

        outer.addStretch()

        # ── Centro: título ──────────────────────────────────────────
        title = QLabel("DEFYVISION  ·  Metalconf")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color:#f1f5f9;font-size:21px;font-weight:700;background:transparent;"
        )
        outer.addWidget(title)

        outer.addStretch()

        # ── Derecha: PLC status + botón + logo ──────────────────────
        right = QWidget()
        right.setStyleSheet("background:transparent;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 8, 0, 8)
        right_lay.setSpacing(4)
        right_lay.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._plc_badge = QLabel("● PLC: —")
        self._plc_badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._plc_badge.setStyleSheet(
            "color:#94a3b8;font-size:11px;font-weight:600;background:transparent;"
        )
        right_lay.addWidget(self._plc_badge)

        reconnect_btn = QPushButton("Reconectar PLC")
        reconnect_btn.setFixedHeight(24)
        reconnect_btn.setStyleSheet(
            "background:#1e40af;color:white;border-radius:5px;"
            "font-size:10px;padding:0 10px;border:none;"
        )
        reconnect_btn.clicked.connect(self._reconnect_plc)
        right_lay.addWidget(reconnect_btn)

        outer.addWidget(right)

        logo_right = QLabel("METALCONF")
        logo_right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        logo_right.setStyleSheet(
            "color:#38bdf8;font-size:17px;font-weight:700;"
            "letter-spacing:2px;background:transparent;"
        )
        outer.addWidget(logo_right)

        return header

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def _refresh_cameras(self) -> None:
        for panel in self._panels.values():
            panel.refresh_camera()

    def _refresh_status(self) -> None:
        if self._system.plc.connected:
            self._plc_badge.setText("● PLC: Conectado")
            self._plc_badge.setStyleSheet(
                "color:#4ade80;font-size:11px;font-weight:600;background:transparent;"
            )
        else:
            self._plc_badge.setText("● PLC: Desconectado")
            self._plc_badge.setStyleSheet(
                "color:#f87171;font-size:11px;font-weight:600;background:transparent;"
            )
        for panel in self._panels.values():
            panel.refresh_status()

    def _reconnect_plc(self) -> None:
        ok = self._system.connect_plc()
        msg = "PLC conectado." if ok else "No se pudo conectar al PLC."
        for panel in self._panels.values():
            panel._log(f"[Sistema] {msg}")

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        reply = QMessageBox.question(
            self, "Cerrar",
            "¿Detener todos los scanners y cerrar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._camera_timer.stop()
            self._status_timer.stop()
            self._system.shutdown()
            event.accept()
        else:
            event.ignore()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _bgr_to_pixmap(frame: np.ndarray, max_w: int, max_h: int) -> QPixmap:
    if frame.ndim == 2:
        qimg = QImage(frame.data, frame.shape[1], frame.shape[0],
                      frame.strides[0], QImage.Format.Format_Grayscale8)
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                      rgb.strides[0], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy()).scaled(
        max_w, max_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


# ------------------------------------------------------------------
# Lanzador
# ------------------------------------------------------------------

def launch_operator_ui(system: InspectionSystem) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = OperatorWindow(system)
    win.show()
    app.exec()
