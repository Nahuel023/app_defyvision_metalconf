"""
Interfaz de operador de producción (PyQt6).

Muestra ambos scanners en paralelo con:
  - Feed de cámara en vivo (~20 fps)
  - Estado del sistema (IDLE / RUNNING / FAULT / ERROR)
  - Modo de operación (MANUAL / AUTO)
  - Métricas de sesión: total, OK, NOK, racha, último resultado
  - Controles: INICIAR / DETENER / RESET
  - Selector de modelo por scanner
  - Log de eventos compacto e independiente por scanner (thread-safe via señal)
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
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

# Raíz del proyecto (src/ui/operator.py → ../../..)
_ROOT = Path(__file__).resolve().parent.parent.parent

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
    OperationMode.AUTO:   "#38bdf8",
    OperationMode.MANUAL: "#94a3b8",
}
_CAMERA_REFRESH_MS = 50      # 20 fps
_STATUS_REFRESH_MS = 200
_OVERLAY_HOLD_MS   = 2500


# ------------------------------------------------------------------
# Panel de un scanner
# ------------------------------------------------------------------

class ScannerPanel(QWidget):
    """Panel completo para un scanner (cámara + estado + métricas + controles + log)."""

    _sig_log     = pyqtSignal(str)
    _sig_overlay = pyqtSignal(object, int)   # (np.ndarray, until_ms)

    def __init__(self, scanner_id: str, system: InspectionSystem,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._id      = scanner_id
        self._system  = system
        self._scanner = system.scanner(scanner_id)
        self._camera  = system.camera(scanner_id)

        self._last_overlay: Optional[np.ndarray] = None
        self._overlay_until_ms: int = 0

        self._build_ui()
        self._populate_models()

        self._sig_log.connect(self._log_widget.append)
        self._sig_overlay.connect(self._set_overlay)

        self._scanner.on_state_changed = self._on_state_changed
        self._scanner.on_result        = self._on_result

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Título ──────────────────────────────────────────────────
        title = QLabel(self._id.replace("_", " ").upper())
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size:13px;font-weight:700;color:#cbd5e1;"
            "background:#1e293b;border-radius:6px;padding:5px;"
            "letter-spacing:1px;"
        )
        root.addWidget(title)

        # ── Feed de cámara ──────────────────────────────────────────
        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setMinimumSize(440, 280)
        self.camera_label.setStyleSheet(
            "background:#050e1a;border-radius:8px;"
            "border:1px solid #1e293b;"
        )
        self.camera_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.camera_label, stretch=1)

        # ── Badge de estado (ancho completo, muy visible) ────────────
        self.state_badge = QLabel("● IDLE")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_badge.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        bg, fg = _COLOR[ScannerState.IDLE]
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;padding:8px 14px;"
        )
        root.addWidget(self.state_badge)

        # ── Panel de métricas operativas (grilla 2×3 sobre fondo oscuro) ──
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet(
            "background:#0a1628;border-radius:8px;"
            "border:1px solid #1e293b;"
        )
        grid = QGridLayout(metrics_frame)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(5)

        # Fila 0: modo | total | último resultado
        self._mode_val   = self._metric_card("MODO",      "MANUAL", _MODE_COLOR[OperationMode.MANUAL])
        self._total_val  = self._metric_card("TOTAL",     "0",      "#64748b")
        self._result_val = self._metric_card("ÚLTIMO",    "—",      "#64748b")
        # Fila 1: racha | OK | NOK
        self._streak_val = self._metric_card("RACHA NOK", "0",      "#64748b")
        self._ok_val     = self._metric_card("✓ OK",      "0",      "#4ade80")
        self._nok_val    = self._metric_card("✗ NOK",     "0",      "#64748b")

        for col, mv in enumerate([self._mode_val, self._total_val, self._result_val]):
            grid.addWidget(mv[0], 0, col)
        for col, mv in enumerate([self._streak_val, self._ok_val, self._nok_val]):
            grid.addWidget(mv[0], 1, col)

        root.addWidget(metrics_frame)

        # ── Selector de modelo ───────────────────────────────────────
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        lbl = QLabel("Modelo:")
        lbl.setStyleSheet("font-size:11px;color:#64748b;font-weight:600;")
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet(
            "font-size:11px;background:#1e293b;color:#cbd5e1;"
            "border:1px solid #334155;border-radius:5px;padding:2px 6px;"
        )
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(lbl)
        model_row.addWidget(self.model_combo, stretch=1)
        root.addLayout(model_row)

        # ── Botones de control ───────────────────────────────────────
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

        # ── Log — compacto, rol secundario ───────────────────────────
        self._log_widget = QTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMaximumHeight(54)
        self._log_widget.setMinimumHeight(54)
        self._log_widget.setFont(QFont("Consolas", 7))
        self._log_widget.setStyleSheet(
            "background:#050e1a;color:#334155;border:none;"
            "border-radius:5px;padding:3px;"
        )
        root.addWidget(self._log_widget)

        self._refresh_buttons(ScannerState.IDLE)

    def _metric_card(self, title: str, value: str, color: str) -> tuple[QWidget, QLabel]:
        """Tarjeta de métrica con estilo oscuro/HMI."""
        w = QWidget()
        w.setStyleSheet(
            "background:#0f172a;border-radius:6px;"
            "border:1px solid #1e293b;"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet("font-size:8px;color:#475569;letter-spacing:0.5px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v = QLabel(value)
        v.setStyleSheet(f"font-size:15px;font-weight:700;color:{color};")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(v)
        return w, v

    def _control_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(34)
        btn.setStyleSheet(
            f"background:{color};color:white;font-weight:700;"
            "border-radius:6px;font-size:12px;border:none;padding:0 8px;"
        )
        return btn

    # ------------------------------------------------------------------
    # Refresco (llamado por timers del padre — hilo principal)
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
        h = max(280, rect.height() - 4)
        self.camera_label.setPixmap(_bgr_to_pixmap(frame, w, h))

    def refresh_status(self) -> None:
        s       = self._scanner.get_status()
        state   = s["state"]
        mode    = s["mode"]
        streak  = s["nok_streak"]
        result  = s["last_result"]
        total   = s["total_inspections"]
        ok_cnt  = s["ok_count"]
        nok_cnt = s["nok_count"]

        # Badge de estado
        bg, fg = _COLOR[state]
        self.state_badge.setText(f"● {state.value.upper()}")
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;"
            "padding:8px 14px;font-size:14px;font-weight:700;"
        )

        # Modo
        mc = _MODE_COLOR[mode]
        self._mode_val[1].setText(mode.value.upper())
        self._mode_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{mc};")

        # Racha NOK
        sc = "#f87171" if streak > 0 else "#64748b"
        self._streak_val[1].setText(str(streak))
        self._streak_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{sc};")

        # Totales de sesión
        self._total_val[1].setText(str(total))

        ok_c = "#4ade80" if ok_cnt > 0 else "#64748b"
        self._ok_val[1].setText(str(ok_cnt))
        self._ok_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{ok_c};")

        nok_c = "#f87171" if nok_cnt > 0 else "#64748b"
        self._nok_val[1].setText(str(nok_cnt))
        self._nok_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{nok_c};")

        # Último resultado
        if result:
            rc = "#4ade80" if result.status == "OK" else "#f87171"
            missing_txt = f" ({result.report.missing}✗)" if result.report.missing else ""
            self._result_val[1].setText(f"{result.status}{missing_txt}")
            self._result_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{rc};")

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
    # Callbacks del controller (llamados desde threads — usan señales)
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: ScannerState, mode: OperationMode) -> None:
        self._log(f"Estado → {state.value.upper()} / {mode.value.upper()}")

    def _on_result(self, result: InspectionResult, streak: int) -> None:
        until_ms = int(time.monotonic() * 1000) + _OVERLAY_HOLD_MS
        self._sig_overlay.emit(result.overlay.copy(), until_ms)
        label = "OK" if result.status == "OK" else f"NOK — {result.report.missing} faltante(s)"
        self._log(f"{label}  |  racha={streak}")

    def _set_overlay(self, overlay: np.ndarray, until_ms: int) -> None:
        self._last_overlay     = overlay
        self._overlay_until_ms = until_ms

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
        self._sig_log.emit(f"[{ts}] {msg}")


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

class OperatorWindow(QMainWindow):
    def __init__(self, system: InspectionSystem) -> None:
        super().__init__()
        self._system      = system
        self._service_win = None
        self.setWindowTitle("DEFYVISION — Metalconf")
        self.setStyleSheet("QMainWindow { background:#0a1628; }")
        self.resize(1400, 880)
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
        central.setStyleSheet("background:#0a1628;")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle { background:#1e293b; width:2px; }")
        self._panels: dict[str, ScannerPanel] = {}

        for sid in self._system.scanner_ids():
            panel = ScannerPanel(sid, self._system)
            panel.setStyleSheet(
                "ScannerPanel { background:#0f172a;border-radius:10px; }"
            )
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame { background:#0f172a;border-radius:10px;"
                "border:1px solid #1e293b; }"
            )
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
        header.setFixedHeight(82)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #050e1a, stop:0.5 #0f2040, stop:1 #050e1a);"
            "border-radius:10px;"
            "border:1px solid #1e293b;"
        )

        outer = QHBoxLayout(header)
        outer.setContentsMargins(20, 0, 20, 0)
        outer.setSpacing(20)

        # ── Logo Metalconf (izquierda — cliente) ────────────────────
        outer.addWidget(_logo_label("logos/metalconf.png", 56))

        outer.addStretch()

        # ── Centro: título ──────────────────────────────────────────
        center = QVBoxLayout()
        center.setSpacing(2)
        center.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("DEFYVISION")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color:#f1f5f9;font-size:22px;font-weight:700;"
            "letter-spacing:3px;background:transparent;"
        )
        subtitle = QLabel("Sistema de Inspección Visual · Metalconf")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color:#475569;font-size:10px;letter-spacing:1px;"
            "background:transparent;"
        )
        center.addWidget(title)
        center.addWidget(subtitle)
        outer.addLayout(center)

        outer.addStretch()

        # ── PLC badge + botones ─────────────────────────────────────
        right = QWidget()
        right.setStyleSheet("background:transparent;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 10, 0, 10)
        right_lay.setSpacing(5)
        right_lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._plc_badge = QLabel("● PLC: —")
        self._plc_badge.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._plc_badge.setStyleSheet(
            "color:#94a3b8;font-size:11px;font-weight:600;background:transparent;"
        )
        right_lay.addWidget(self._plc_badge)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        reconnect_btn = QPushButton("Reconectar PLC")
        reconnect_btn.setFixedHeight(22)
        reconnect_btn.setStyleSheet(
            "background:#1e40af;color:white;border-radius:5px;"
            "font-size:10px;padding:0 10px;border:none;"
        )
        reconnect_btn.clicked.connect(self._reconnect_plc)

        service_btn = QPushButton("Modo Servicio")
        service_btn.setFixedHeight(22)
        service_btn.setStyleSheet(
            "background:#1e293b;color:#94a3b8;border-radius:5px;"
            "font-size:10px;padding:0 10px;border:1px solid #334155;"
        )
        service_btn.clicked.connect(self._open_service)

        btn_row.addWidget(reconnect_btn)
        btn_row.addWidget(service_btn)
        right_lay.addLayout(btn_row)

        outer.addWidget(right)

        # ── Logo DEFYMOTION (derecha — desarrollador) ───────────────
        outer.addWidget(_logo_label("logos/defymotion.jpg", 48))

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
        ok  = self._system.connect_plc()
        msg = "PLC conectado." if ok else "No se pudo conectar al PLC."
        for panel in self._panels.values():
            panel._log(f"[Sistema] {msg}")

    def _open_service(self) -> None:
        from src.ui.login_dialog import LoginDialog
        from src.ui.service import ServiceWindow
        from PyQt6.QtWidgets import QDialog

        dlg = LoginDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if self._service_win is None or not self._service_win.isVisible():
            self._service_win = ServiceWindow(self._system, parent=None)
        self._service_win.show()
        self._service_win.raise_()
        self._service_win.activateWindow()

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

def _logo_label(rel_path: str, max_h: int) -> QLabel:
    """Carga un logo desde la raíz del proyecto, escalado a max_h px de alto.
    Si el archivo no existe cae a texto para no romper el layout.
    """
    lbl = QLabel()
    lbl.setStyleSheet("background:transparent;")
    pix = QPixmap(str(_ROOT / rel_path))
    if not pix.isNull():
        pix = pix.scaledToHeight(max_h, Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(pix)
    else:
        lbl.setText(Path(rel_path).stem.upper())
        lbl.setStyleSheet(
            "color:#38bdf8;font-size:14px;font-weight:700;"
            "letter-spacing:2px;background:transparent;"
        )
    lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
    return lbl


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
        Qt.TransformationMode.FastTransformation,
    )


# ------------------------------------------------------------------
# Lanzador
# ------------------------------------------------------------------

def launch_operator_ui(system: InspectionSystem) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = OperatorWindow(system)
    win.show()
    app.exec()
