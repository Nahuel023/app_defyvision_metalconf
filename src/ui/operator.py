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

Tema: claro fijo (fondo blanco/gris claro) — legibilidad en entorno industrial.
Header: oscuro — ancla visual y contraste para logos.
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QImage, QPixmap
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

_ROOT = Path(__file__).resolve().parent.parent.parent

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
_COLOR = {
    ScannerState.IDLE:    ("#64748b", "#ffffff"),
    ScannerState.RUNNING: ("#15803d", "#ffffff"),
    ScannerState.FAULT:   ("#b91c1c", "#ffffff"),
    ScannerState.STOPPED: ("#1e293b", "#94a3b8"),
    ScannerState.ERROR:   ("#92400e", "#ffffff"),
}
_STATE_LABEL = {
    ScannerState.IDLE:    "IDLE",
    ScannerState.RUNNING: "RUNNING",
    ScannerState.FAULT:   "FAULT",
    ScannerState.STOPPED: "PARADO",
    ScannerState.ERROR:   "ERROR",
}
_MODE_COLOR = {
    OperationMode.AUTO:   "#1d4ed8",
    OperationMode.MANUAL: "#64748b",
}
_CAMERA_REFRESH_MS = 50
_STATUS_REFRESH_MS = 200
_OVERLAY_HOLD_MS   = 2500

# Ancho fijo de cada ala del header — igualar ambos lados centra el título
_HEADER_WING_W = 310


# ------------------------------------------------------------------
# Panel de un scanner
# ------------------------------------------------------------------

class ScannerPanel(QWidget):
    """Panel completo para un scanner (cámara + estado + métricas + controles + log)."""

    _sig_log     = pyqtSignal(str)
    _sig_overlay = pyqtSignal(object, int)

    def __init__(self, scanner_id: str, system: InspectionSystem,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._id      = scanner_id
        self._system  = system
        self._scanner = system.scanner(scanner_id)
        self._camera  = system.camera(scanner_id)

        self._last_overlay: Optional[np.ndarray] = None
        self._overlay_until_ms: int = 0
        self._nok_threshold: int = 5   # actualizado en refresh_status
        self._manual_mode_display: bool = False  # True cuando se muestra aviso MODO MANUAL

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

        # ── Título del scanner ───────────────────────────────────────
        _num = self._id.split("_")[-1]
        title = QLabel(f"{self._id.replace('_', ' ').upper()}   ·   BAL {_num}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size:13px;font-weight:700;color:#1e293b;"
            "background:#e2e8f0;border-radius:6px;padding:5px;"
            "letter-spacing:1px;"
        )
        root.addWidget(title)

        # ── Feed de cámara ─ fondo oscuro para contraste ─────────────
        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setMinimumSize(440, 280)
        self.camera_label.setStyleSheet(
            "background:#0a0f1a;border-radius:8px;"
            "border:1px solid #cbd5e1;"
        )
        self.camera_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.camera_label, stretch=1)

        # ── Badge de estado ──────────────────────────────────────────
        self.state_badge = QLabel("● IDLE")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_badge.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        bg, fg = _COLOR[ScannerState.IDLE]
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;padding:8px 14px;"
        )
        root.addWidget(self.state_badge)

        # ── Panel de métricas operativas (grilla 2×3) ────────────────
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet(
            "QFrame { background:#f1f5f9;border-radius:8px;"
            "border:1px solid #cbd5e1; }"
        )
        grid = QGridLayout(metrics_frame)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(5)

        self._mode_val   = self._metric_card("MODO",       "MANUAL", _MODE_COLOR[OperationMode.MANUAL])
        self._total_val  = self._metric_card("TOTAL",      "0",      "#94a3b8")
        self._result_val = self._metric_card("ÚLTIMO",     "—",      "#94a3b8")
        self._streak_val = self._metric_card("RACHA NOK",  "0",      "#94a3b8")
        self._ok_val     = self._metric_card("✓ OK frames","0",      "#94a3b8")
        self._nok_val    = self._metric_card("✗ ALARMAS",  "0",      "#94a3b8")

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
            "font-size:11px;background:#ffffff;color:#1e293b;"
            "border:1px solid #cbd5e1;border-radius:5px;padding:2px 6px;"
        )
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(lbl)
        model_row.addWidget(self.model_combo, stretch=1)
        root.addLayout(model_row)

        # ── Botones de control ───────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.start_btn = self._control_btn("▶  INICIAR", "#15803d")
        self.stop_btn  = self._control_btn("■  DETENER", "#b91c1c")
        self.reset_btn = self._control_btn("↺  RESET",   "#1d4ed8")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.reset_btn)
        root.addLayout(btn_row)

        # ── Log — compacto, visual secundario ────────────────────────
        self._log_widget = QTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setFixedHeight(54)
        self._log_widget.setFont(QFont("Consolas", 7))
        self._log_widget.setStyleSheet(
            "background:#f8fafc;color:#94a3b8;"
            "border:1px solid #e2e8f0;border-radius:5px;padding:3px;"
        )
        root.addWidget(self._log_widget)

        self._refresh_buttons(ScannerState.IDLE)

    def _metric_card(self, title: str, value: str, color: str) -> tuple[QWidget, QLabel]:
        """Tarjeta de métrica — tema claro."""
        w = QWidget()
        w.setStyleSheet(
            "background:#ffffff;border-radius:6px;"
            "border:1px solid #e2e8f0;"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet("font-size:8px;color:#94a3b8;letter-spacing:0.5px;")
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
    # Refresco (hilo principal)
    # ------------------------------------------------------------------

    def refresh_camera(self) -> None:
        state = self._scanner.state
        mode  = self._scanner.mode
        is_manual_running = (state == ScannerState.RUNNING
                             and mode == OperationMode.MANUAL)

        if is_manual_running:
            if not self._manual_mode_display:
                self._manual_mode_display = True
                self.camera_label.setPixmap(QPixmap())
                self.camera_label.setText("MODO MANUAL\nInspección inactiva")
                self.camera_label.setStyleSheet(
                    "background:#0a0f1a;color:#475569;border-radius:8px;"
                    "border:1px solid #cbd5e1;font-size:14px;font-weight:600;"
                    "letter-spacing:1px;"
                )
            return

        if self._manual_mode_display:
            self._manual_mode_display = False
            self.camera_label.clear()
            self.camera_label.setStyleSheet(
                "background:#0a0f1a;border-radius:8px;border:1px solid #cbd5e1;"
            )

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
        s           = self._scanner.get_status()
        state       = s["state"]
        mode        = s["mode"]
        streak      = s["nok_streak"]
        result      = s["last_result"]
        total       = s["total_inspections"]
        ok_cnt      = s["ok_count"]
        fault_cnt   = s["fault_count"]

        from src.utils.config import load_tolerances
        _model = self._system.io.scanner_config(self._id).get("model", "")
        _tols  = load_tolerances(_model) if _model else load_tolerances()
        _threshold = int(_tols.get("consecutive_nok_frames", 5))
        self._nok_threshold = _threshold

        bg, fg = _COLOR.get(state, ("#64748b", "#ffffff"))
        label = _STATE_LABEL.get(state, state.value.upper())
        self.state_badge.setText(f"● {label}")
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:7px;"
            "padding:8px 14px;font-size:14px;font-weight:700;"
        )

        mc = _MODE_COLOR[mode]
        self._mode_val[1].setText(mode.value.upper())
        self._mode_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{mc};")

        # Racha: verde→amarillo→rojo según proporción del umbral
        ratio = streak / _threshold if _threshold > 0 else 0
        if ratio == 0:
            sc = "#94a3b8"
        elif ratio < 0.5:
            sc = "#15803d"
        elif ratio < 0.8:
            sc = "#d97706"
        else:
            sc = "#b91c1c"
        self._streak_val[1].setText(f"{streak} / {_threshold}")
        self._streak_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{sc};")

        self._total_val[1].setText(str(total))

        ok_c = "#15803d" if ok_cnt > 0 else "#94a3b8"
        self._ok_val[1].setText(str(ok_cnt))
        self._ok_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{ok_c};")

        fault_c = "#b91c1c" if fault_cnt > 0 else "#15803d"
        self._nok_val[1].setText(str(fault_cnt))
        self._nok_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{fault_c};")

        # ÚLTIMO: salud temporal del sistema
        if state == ScannerState.STOPPED:
            health_txt, health_c = "PARADO", "#475569"
            self._result_val[1].setText(health_txt)
            self._result_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{health_c};")
        elif state == ScannerState.RUNNING and mode == OperationMode.MANUAL:
            health_txt, health_c = "MANUAL", "#64748b"
            self._result_val[1].setText(health_txt)
            self._result_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{health_c};")
        elif result is not None:
            if state == ScannerState.FAULT:
                health_txt, health_c = "FAULT", "#b91c1c"
            elif ratio >= 0.8:
                health_txt, health_c = f"ALERTA {streak}/{_threshold}", "#b91c1c"
            elif ratio >= 0.5:
                health_txt, health_c = f"ATENCIÓN {streak}/{_threshold}", "#d97706"
            else:
                health_txt, health_c = "OK", "#15803d"
            self._result_val[1].setText(health_txt)
            self._result_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{health_c};")

        self._refresh_buttons(state)

    # ------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if not self._scanner.start():
            QMessageBox.warning(self, "Iniciar", f"No se pudo iniciar {self._id}.")
        else:
            mode = self._scanner.mode
            self._log(f"INICIADO ({mode.value.upper()})")

    def _on_stop(self) -> None:
        self._scanner.stop()
        self._log("DETENIDO")

    def _on_reset(self) -> None:
        if self._scanner.reset():
            self._log("RESET → IDLE")
        else:
            QMessageBox.information(self, "Reset", "Solo disponible en estado PARADO.")

    def _on_model_changed(self, display_name: str) -> None:
        from src.utils.model_names import to_internal
        internal = to_internal(display_name)
        if internal:
            self._scanner.set_model(internal)
            self._log(f"Modelo → {display_name}")

    # ------------------------------------------------------------------
    # Callbacks del controller (threads → señales → hilo principal)
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: ScannerState, mode: OperationMode) -> None:
        label = _STATE_LABEL.get(state, state.value.upper())
        self._log(f"Estado → {label} / {mode.value.upper()}")

    def _on_result(self, result: InspectionResult, streak: int) -> None:
        threshold = self._nok_threshold
        warn_level = threshold // 3   # mostrar overlay con marcas rojas a partir de aquí

        # Overlay: solo mostrar marcas de falla cuando la racha es significativa
        if streak >= warn_level or result.status == "OK":
            until_ms = int(time.monotonic() * 1000) + _OVERLAY_HOLD_MS
            self._sig_overlay.emit(result.overlay.copy(), until_ms)

        # Log: solo en hitos relevantes, no en cada frame
        if streak == 0 and result.status == "OK":
            pass   # silencioso cuando todo va bien
        elif streak == 0:
            self._log("Racha NOK terminada — material OK")
        elif streak % 10 == 0:
            self._log(f"Racha NOK: {streak} / {threshold}")

    def _set_overlay(self, overlay: np.ndarray, until_ms: int) -> None:
        self._last_overlay     = overlay
        self._overlay_until_ms = until_ms

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_buttons(self, state: ScannerState) -> None:
        self.start_btn.setEnabled(state == ScannerState.IDLE)
        self.stop_btn.setEnabled(state in (ScannerState.RUNNING, ScannerState.FAULT))
        self.reset_btn.setEnabled(state == ScannerState.STOPPED)

    def _populate_models(self) -> None:
        from src.utils.model_names import DISPLAY_NAMES, to_display
        cfg = self._system.io.scanner_config(self._id)
        current_internal = cfg.get("model", "")
        allowed_internal = cfg.get("allowed_models", None)

        if allowed_internal:
            display_list = [to_display(m) for m in allowed_internal]
        else:
            display_list = DISPLAY_NAMES

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(display_list)
        if current_internal:
            disp = to_display(current_internal)
            if self.model_combo.findText(disp) >= 0:
                self.model_combo.setCurrentText(disp)
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
        self._metrics_win = None
        self.setWindowTitle("DEFYVISION")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.setStyleSheet(
            "QToolTip { background:#1e293b; color:#f1f5f9; border:none; }"
        )
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
        central.setStyleSheet("background:#eef2f7;")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet(
            "QSplitter::handle { background:#cbd5e1; width:2px; }"
        )
        self._panels: dict[str, ScannerPanel] = {}

        for sid in self._system.scanner_ids():
            panel = ScannerPanel(sid, self._system)
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame { background:#ffffff;border-radius:10px;"
                "border:1px solid #cbd5e1; }"
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
        """
        Header oscuro con logos reales.

        Layout de 3 secciones de ancho fijo igual (_HEADER_WING_W px c/u):
          [ala izquierda] | [centro: título, stretch=1] | [ala derecha]

        Ambas alas tienen el mismo ancho → el título queda perfectamente centrado.
        """
        header = QWidget()
        header.setFixedHeight(84)
        header.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #05101f, stop:0.5 #0c2340, stop:1 #05101f);"
            "border-radius:10px; }"
        )

        outer = QHBoxLayout(header)
        outer.setContentsMargins(18, 0, 18, 0)
        outer.setSpacing(0)

        # ── Ala izquierda: logo Metalconf ────────────────────────────
        left_wing = QWidget()
        left_wing.setFixedWidth(_HEADER_WING_W)
        left_wing.setStyleSheet("background:transparent;")
        left_lay = QHBoxLayout(left_wing)
        left_lay.setContentsMargins(0, 10, 0, 10)
        left_lay.setSpacing(0)
        left_lay.addWidget(_logo_label("logos/metalconf.png", 56))
        left_lay.addStretch()
        outer.addWidget(left_wing)

        # ── Centro: título ────────────────────────────────────────────
        center = QWidget()
        center.setStyleSheet("background:transparent;")
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(3)
        center_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("DEFYVISION")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color:#f1f5f9;font-size:26px;font-weight:700;"
            "letter-spacing:4px;background:transparent;"
        )
        subtitle = QLabel("Visión Artificial  ·  Robótica Industrial")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color:#475569;font-size:10px;letter-spacing:1.5px;"
            "background:transparent;"
        )
        center_lay.addWidget(title)
        center_lay.addWidget(subtitle)
        outer.addWidget(center, stretch=1)

        # ── Ala derecha: controles + logo DEFYMOTION ─────────────────
        right_wing = QWidget()
        right_wing.setFixedWidth(_HEADER_WING_W)
        right_wing.setStyleSheet("background:transparent;")
        right_lay = QHBoxLayout(right_wing)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(10)

        # Sub-columna izquierda: PLC badge + botones
        ctrl = QWidget()
        ctrl.setStyleSheet("background:transparent;")
        ctrl_lay = QVBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(0, 10, 0, 10)
        ctrl_lay.setSpacing(5)
        ctrl_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Badge solo visible cuando hay problema de PLC
        self._plc_badge = QLabel("● PLC: Desconectado")
        self._plc_badge.setStyleSheet(
            "color:#f87171;font-size:11px;font-weight:600;background:transparent;"
        )
        self._plc_badge.hide()
        ctrl_lay.addWidget(self._plc_badge)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)

        metrics_btn = QPushButton("Métricas")
        metrics_btn.setFixedHeight(22)
        metrics_btn.setStyleSheet(
            "background:#1e40af;color:white;border-radius:5px;"
            "font-size:10px;padding:0 8px;border:none;"
        )
        metrics_btn.clicked.connect(self._open_metrics)

        service_btn = QPushButton("Modo Servicio")
        service_btn.setFixedHeight(22)
        service_btn.setStyleSheet(
            "background:#334155;color:#94a3b8;border-radius:5px;"
            "font-size:10px;padding:0 8px;border:none;"
        )
        service_btn.clicked.connect(self._open_service)

        btn_row.addWidget(metrics_btn)
        btn_row.addWidget(service_btn)
        ctrl_lay.addLayout(btn_row)

        right_lay.addStretch()
        right_lay.addWidget(ctrl)

        # Sub-columna derecha: logo DEFYMOTION
        dm_logo = _logo_label("logos/defymotion.jpg", 48)
        dm_logo.setContentsMargins(6, 0, 0, 0)
        right_lay.addWidget(dm_logo)

        outer.addWidget(right_wing)

        return header

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------

    def _refresh_cameras(self) -> None:
        for panel in self._panels.values():
            panel.refresh_camera()

    def _refresh_status(self) -> None:
        if self._system.plc.connected:
            self._plc_badge.hide()
        else:
            self._plc_badge.setText("● PLC: Desconectado")
            self._plc_badge.setStyleSheet(
                "color:#f87171;font-size:11px;font-weight:600;background:transparent;"
            )
            self._plc_badge.show()
        for panel in self._panels.values():
            panel.refresh_status()

    def _open_metrics(self) -> None:
        from src.ui.metrics_window import MetricsWindow
        if self._metrics_win is None or not self._metrics_win.isVisible():
            self._metrics_win = MetricsWindow(self._system)
        self._metrics_win.show()
        self._metrics_win.raise_()
        self._metrics_win.activateWindow()

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

    def closeEvent(self, event) -> None:
        reply = QMessageBox.question(
            self, "Cerrar",
            "¿Apagar el sistema y cerrar?\n\n"
            "Se detendrán los scanners y se apagarán todas las salidas.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._camera_timer.stop()
            self._status_timer.stop()
            # Cerrar ventanas auxiliares antes de apagar el sistema
            if self._service_win is not None and self._service_win.isVisible():
                self._service_win.close()
            if self._metrics_win is not None and self._metrics_win.isVisible():
                self._metrics_win.close()
            self._system.shutdown()
            event.accept()
        else:
            event.ignore()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _logo_label(rel_path: str, max_h: int) -> QLabel:
    """Carga logo desde raíz del proyecto escalado a max_h px de alto.
    Fallback a texto si el archivo no existe.
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
    # Registrar AppUserModelID antes de crear QApplication para que Windows
    # muestre el ícono correcto en la barra de tareas en vez del ícono de Python.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "DEFYMOTION.DEFYVISION.1.0"
        )
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
    if not icon_pix.isNull():
        icon = QIcon(icon_pix)
        app.setWindowIcon(icon)
    win = OperatorWindow(system)
    win.showMaximized()
    win.raise_()
    win.activateWindow()
    app.exec()
