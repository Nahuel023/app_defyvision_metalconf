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
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.inspection import InspectionResult
from src.utils.state import OperationMode, ScannerState

from src.utils.paths import app_root
_ROOT = app_root()

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
# Paleta industrial oscura
_BG      = "#0d1117"   # fondo ventana
_PANEL   = "#161b22"   # fondo panel scanner
_CARD    = "#21262d"   # fondo tarjeta métrica
_BORDER  = "#30363d"   # borde normal
_TEXT    = "#f0f6fc"   # texto principal
_MUTED   = "#8b949e"   # texto secundario
_OK_CLR  = "#3fb950"   # verde OK
_NOK_CLR = "#f85149"   # rojo NOK/FAULT
_WARN    = "#d29922"   # amarillo advertencia

_COLOR = {
    ScannerState.IDLE:    ("#21262d", "#8b949e"),
    ScannerState.RUNNING: ("#0d3320", "#3fb950"),
    ScannerState.FAULT:   ("#3d0c0c", "#f85149"),
    ScannerState.STOPPED: ("#161b22", "#6b7280"),
    ScannerState.ERROR:   ("#3d2000", "#d29922"),
}
_STATE_LABEL = {
    ScannerState.IDLE:    "EN ESPERA",
    ScannerState.RUNNING: "INSPECCIONANDO",
    ScannerState.FAULT:   "FALLA DETECTADA",
    ScannerState.STOPPED: "DETENIDO",
    ScannerState.ERROR:   "ERROR",
}
_MODE_COLOR = {
    OperationMode.AUTO:   "#3b82f6",
    OperationMode.MANUAL: "#6b7280",
}
_CAMERA_REFRESH_MS      = 50
_STATUS_REFRESH_MS      = 200
_OVERLAY_HOLD_MS        = 2500
_OVERLAY_HOLD_FAULT_MS  = 30_000

_HEADER_WING_W = 370


# ------------------------------------------------------------------
# Panel de un scanner
# ------------------------------------------------------------------

class ScannerPanel(QWidget):
    """Panel de un scanner: cámara + estado + métricas + controles."""

    _sig_overlay    = pyqtSignal(object, int)
    _sig_stop_alert = pyqtSignal(object, str, str)  # (overlay, label, reason)

    def __init__(self, scanner_id: str, system: InspectionSystem,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._id      = scanner_id
        self._system  = system
        self._scanner = system.scanner(scanner_id)
        self._camera  = system.camera(scanner_id)

        self._last_overlay: Optional[np.ndarray] = None
        self._overlay_until_ms: int = 0
        self._nok_threshold: int = 5
        self._last_shown_pixmap = None   # retiene último frame para no quedar en negro
        self._run_start_time: float | None = None

        self._build_ui()
        self._populate_models()

        self._sig_overlay.connect(self._set_overlay)

        self._scanner.on_state_changed = self._on_state_changed
        self._scanner.on_result        = self._on_result

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_PANEL};")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Encabezado: número de scanner + tipo de placa ─────────────
        from src.utils.model_names import to_display
        _num = self._id.split("_")[-1]
        _model_internal = self._system.io.scanner_config(self._id).get("model", "")
        _model_display  = to_display(_model_internal) if _model_internal else "—"

        self._title_lbl = QLabel(f"SCANNER {_num}   ·   {_model_display}")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{_MUTED};"
            f"background:{_CARD};border-radius:6px;padding:6px;"
            "letter-spacing:2px;"
        )
        root.addWidget(self._title_lbl)

        # ── Feed de cámara — elemento dominante ───────────────────────
        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_label.setMinimumSize(440, 290)
        self.camera_label.setStyleSheet(
            f"background:#000000;border-radius:6px;"
            f"border:2px solid {_BORDER};"
        )
        self.camera_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.camera_label, stretch=1)

        # ── Info de cámara: IP · FPS · estado de conexión ────────────
        self._cam_info_lbl = QLabel("● —")
        self._cam_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_info_lbl.setStyleSheet(
            f"font-size:10px;font-family:Consolas,monospace;color:{_MUTED};"
            f"background:{_CARD};border-radius:4px;padding:3px 8px;"
            f"border:1px solid {_BORDER};"
        )
        root.addWidget(self._cam_info_lbl)

        # ── Badge de estado — muy prominente ──────────────────────────
        self.state_badge = QLabel("● EN ESPERA")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_badge.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        bg, fg = _COLOR[ScannerState.IDLE]
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:6px;"
            "padding:10px 14px;letter-spacing:1px;"
        )
        root.addWidget(self.state_badge)

        # ── Tres métricas operativas: OK / TIEMPO / NOK ────────────
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(5)
        self._ok_val    = self._metric_card("OK",      "0",      _OK_CLR)
        self._nok_val   = self._metric_card("Tiempo en funcionamiento continuo",  "00:00",  _MUTED, word_wrap=True, title_size=11)
        self._total_val = self._metric_card("NOK",     "0",      _NOK_CLR)
        # campos no visibles pero necesarios para refresh_status
        self._mode_val   = self._metric_card("MODO",      "AUTO", _MUTED)
        self._result_val = self._metric_card("ÚLTIMO",    "—",    _MUTED)
        self._streak_val = self._metric_card("RACHA NOK", "0",    _MUTED)
        self._center_val = self._metric_card("CENTRADO",  "—",    _MUTED)
        for mv in (self._ok_val, self._nok_val, self._total_val):
            metrics_row.addWidget(mv[0])

        self._metrics_btn = QPushButton("ℹ")
        self._metrics_btn.setFixedSize(26, 26)
        self._metrics_btn.setToolTip("Ver métricas detalladas")
        self._metrics_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:{_MUTED}; border:1px solid {_BORDER};"
            "border-radius:5px; font-size:13px; font-weight:700; padding:0; }"
            f"QPushButton:hover {{ background:{_BORDER}; color:{_TEXT}; }}"
        )
        self._metrics_btn.clicked.connect(self._show_metrics_popup)
        metrics_row.addWidget(self._metrics_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(metrics_row)

        # ── Selector de placa ─────────────────────────────────────────
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        lbl = QLabel("Placa:")
        lbl.setStyleSheet(f"font-size:11px;color:{_MUTED};font-weight:600;background:transparent;")
        self.model_combo = QComboBox()
        self.model_combo.setMinimumHeight(30)
        self.model_combo.setStyleSheet(
            f"font-size:12px;font-weight:600;"
            f"background:{_CARD};color:{_TEXT};"
            f"border:1px solid {_BORDER};border-radius:5px;padding:2px 8px;"
        )
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(lbl)
        model_row.addWidget(self.model_combo, stretch=1)
        root.addLayout(model_row)

        # ── RESET FALLA — aparece encima de INICIAR/DETENER al detenerse ──
        self.reset_btn = self._primary_btn("↺  RESET FALLA", "#b45309")
        self.reset_btn.clicked.connect(self._on_reset)
        self.reset_btn.setVisible(False)
        root.addWidget(self.reset_btn)

        # ── Botones principales: INICIAR / DETENER ────────────────────
        main_btn_row = QHBoxLayout()
        main_btn_row.setSpacing(8)
        self.start_btn = self._primary_btn("▶  INICIAR", "#166534")
        self.stop_btn  = self._primary_btn("■  DETENER", "#991b1b")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        main_btn_row.addWidget(self.start_btn)
        main_btn_row.addWidget(self.stop_btn)
        root.addLayout(main_btn_row)

        self._refresh_buttons(ScannerState.IDLE)

    def _metric_card(self, title: str, value: str, color: str, word_wrap: bool = False, title_size: int = 8) -> tuple[QWidget, QLabel]:
        """Tarjeta de métrica — tema oscuro industrial."""
        w = QWidget()
        w.setStyleSheet(
            f"background:{_CARD};border-radius:6px;"
            f"border:1px solid {_BORDER};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:{title_size}px;color:{_MUTED};letter-spacing:0px;background:transparent;")
        t.setWordWrap(word_wrap)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v = QLabel(value)
        v.setStyleSheet(f"font-size:18px;font-weight:700;color:{color};background:transparent;")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(v)
        return w, v

    def _primary_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(52)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(
            f"QPushButton {{ background:{color}; color:#ffffff; font-weight:700; "
            "border-radius:8px; font-size:16px; border:none; padding:0 12px; }"
            "QPushButton:hover { filter:brightness(1.15); }"
            "QPushButton:disabled { background:#374151; color:#6b7280; }"
        )
        return btn

    def _secondary_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(26)
        btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{color}; font-weight:600; "
            f"border-radius:5px; font-size:11px; border:1px solid {color}; padding:0 12px; }}"
            f"QPushButton:hover {{ background:{color}22; }}"
            "QPushButton:disabled { color:#4b5563; border-color:#374151; }"
        )
        return btn

    def _control_btn(self, text: str, color: str) -> QPushButton:
        return self._primary_btn(text, color)

    # ------------------------------------------------------------------
    # Refresco (hilo principal)
    # ------------------------------------------------------------------

    @staticmethod
    def _cam_host_label(index: int | str) -> str:
        if isinstance(index, int):
            return f"USB:{index}"
        try:
            from urllib.parse import urlparse
            h = urlparse(str(index)).hostname or str(index)
            return h
        except Exception:
            return str(index)

    def _update_cam_info(self, connected: bool, missing: bool) -> None:
        host = self._cam_host_label(self._camera.index)
        fps  = self._camera.fps
        if missing:
            dot, color = "●", _WARN
            text = f"{dot}  {host}  —  DESCONECTADA"
        elif connected:
            dot, color = "●", _OK_CLR
            text = f"{dot}  {host}  —  {fps:.1f} fps"
        else:
            dot, color = "●", _MUTED
            text = f"{dot}  {host}  —  INICIANDO..."
        self._cam_info_lbl.setText(text)
        self._cam_info_lbl.setStyleSheet(
            f"font-size:10px;font-family:Consolas,monospace;color:{color};"
            f"background:{_CARD};border-radius:4px;padding:3px 8px;"
            f"border:1px solid {_BORDER};"
        )

    def refresh_camera(self) -> None:
        status = self._scanner.get_status()
        camera_missing = bool(status.get("camera_missing", False))
        camera_missing_sec = float(status.get("camera_missing_sec", 0.0))

        self._update_cam_info(
            connected=self._camera.is_connected,
            missing=camera_missing,
        )

        # Siempre mostrar el feed de cámara en vivo
        frame = self._camera.get_frame()

        rect = self.camera_label.contentsRect()
        w = max(440, rect.width() - 4)
        h = max(280, rect.height() - 4)

        if frame is not None:
            # Frame nuevo disponible — actualizar y cachear
            px = _bgr_to_pixmap(frame, w, h)
            self._last_shown_pixmap = px
            self.camera_label.setPixmap(px)
            self.camera_label.setText("")
            self.camera_label.setStyleSheet(
                f"background:#000000;border-radius:6px;border:2px solid {_BORDER};"
            )
        elif self._last_shown_pixmap is not None:
            # Sin frame nuevo: retener último frame conocido (corte transitorio o camera_missing)
            self.camera_label.setPixmap(self._last_shown_pixmap)
            self.camera_label.setText("")
        else:
            # Sin imagen: nunca conectó, o perdida por tiempo suficiente
            host = self._cam_host_label(self._camera.index)
            if camera_missing:
                secs = f"{camera_missing_sec:.0f}s"
                line2 = f"Reconectando...  ({secs})"
            else:
                line2 = "Conectando..."
            self.camera_label.setPixmap(QPixmap())
            self.camera_label.setText(f"CAMARA DESCONECTADA\n{host}\n{line2}")
            self.camera_label.setStyleSheet(
                f"background:#0d1117;color:{_WARN};border-radius:6px;"
                f"border:2px solid {_WARN};font-size:14px;font-weight:700;"
                "letter-spacing:1px;"
            )

    def refresh_status(self) -> None:
        s           = self._scanner.get_status()
        state       = s["state"]
        mode        = s["mode"]
        streak      = s["nok_streak"]
        result      = s["last_result"]
        total       = s["total_inspections"]
        ok_cnt      = s["ok_count"]
        fault_cnt   = s["fault_count"]
        camera_missing = bool(s.get("camera_missing", False))
        camera_missing_sec = float(s.get("camera_missing_sec", 0.0))

        from src.utils.config import load_tolerances
        _model = self._system.io.scanner_config(self._id).get("model", "")
        _tols  = load_tolerances(_model, scanner_id=self._id) if _model else load_tolerances()
        _threshold = int(_tols.get("consecutive_nok_frames", 5))
        self._nok_threshold = _threshold

        bg, fg = _COLOR.get(state, ("#64748b", "#ffffff"))
        label = _STATE_LABEL.get(state, state.value.upper())
        self.state_badge.setText(f"● {label}")
        self.state_badge.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:6px;"
            "padding:10px 14px;font-size:16px;font-weight:700;letter-spacing:1px;"
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

        nok_cnt = s["nok_count"]
        nok_c = _NOK_CLR if nok_cnt > 0 else _MUTED
        self._total_val[1].setText(str(nok_cnt))
        self._total_val[1].setStyleSheet(f"font-size:18px;font-weight:700;color:{nok_c};background:transparent;")

        ok_c = _OK_CLR if ok_cnt > 0 else _MUTED
        self._ok_val[1].setText(str(ok_cnt))
        self._ok_val[1].setStyleSheet(f"font-size:18px;font-weight:700;color:{ok_c};background:transparent;")

        if self._run_start_time is not None and state == ScannerState.RUNNING:
            elapsed = int(time.monotonic() - self._run_start_time)
            h, rem = divmod(elapsed, 3600)
            m, sec = divmod(rem, 60)
            time_txt = f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"
        else:
            time_txt = "00:00"
        self._nok_val[1].setText(time_txt)
        self._nok_val[1].setStyleSheet(f"font-size:18px;font-weight:700;color:{_MUTED};background:transparent;")

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
            if camera_missing and state == ScannerState.RUNNING:
                health_txt, health_c = f"CAMARA {camera_missing_sec:.1f}s", "#d29922"
            elif state == ScannerState.FAULT:
                health_txt, health_c = "FAULT", "#b91c1c"
            elif ratio >= 0.8:
                health_txt, health_c = f"ALERTA {streak}/{_threshold}", "#b91c1c"
            elif ratio >= 0.5:
                health_txt, health_c = f"ATENCIÓN {streak}/{_threshold}", "#d97706"
            else:
                health_txt, health_c = "OK", "#15803d"
            self._result_val[1].setText(health_txt)
            self._result_val[1].setStyleSheet(f"font-size:15px;font-weight:700;color:{health_c};")
        elif camera_missing and state == ScannerState.RUNNING:
            self._result_val[1].setText(f"CAMARA {camera_missing_sec:.1f}s")
            self._result_val[1].setStyleSheet("font-size:15px;font-weight:700;color:#d29922;")

        # CENTRADO: márgenes laterales y offset del patrón respecto a los bordes de la chapa
        if result is not None and result.centering is not None:
            c = result.centering
            sign = "+" if c.offset_px >= 0 else ""
            c_color = "#15803d" if c.within_tol else "#ea580c"
            c_text = (
                f"I: {c.left_margin_px:.0f}px  D: {c.right_margin_px:.0f}px\n"
                f"Offset: {sign}{c.offset_px:.1f}px"
            )
            self._center_val[1].setText(c_text)
            self._center_val[1].setStyleSheet(f"font-size:11px;font-weight:700;color:{c_color};")
        elif result is None:
            self._center_val[1].setText("—")
            self._center_val[1].setStyleSheet("font-size:15px;font-weight:700;color:#94a3b8;")

        self._refresh_buttons(state)

    # ------------------------------------------------------------------
    # Popup de métricas detalladas
    # ------------------------------------------------------------------

    def _show_metrics_popup(self) -> None:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel as _QLabel

        s = self._scanner.get_status()
        streak   = s["nok_streak"]
        mode     = s["mode"]
        total    = s["total_inspections"]
        ok_cnt   = s["ok_count"]
        nok_cnt  = s["nok_count"]
        threshold = getattr(self, "_nok_threshold", 5)

        dlg = QDialog(self)
        _num = self._id.split("_")[-1]
        dlg.setWindowTitle(f"Métricas — Scanner {_num}")
        dlg.setFixedWidth(230)
        dlg.setStyleSheet(
            f"QDialog {{ background:{_CARD};color:{_TEXT}; }}"
            f"QLabel  {{ background:transparent; }}"
        )

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        def _row(label: str, value: str, color: str = _TEXT) -> None:
            h = QHBoxLayout()
            h.setSpacing(6)
            lbl = _QLabel(label)
            lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
            val = _QLabel(value)
            val.setStyleSheet(f"color:{color};font-weight:700;font-size:12px;")
            h.addWidget(lbl)
            h.addStretch()
            h.addWidget(val)
            lay.addLayout(h)

        mode_c = "#15803d" if mode.value == "auto" else "#d97706"
        _row("Modo", mode.value.upper(), mode_c)
        _row("Inspecciones", str(total))
        _row("OK", str(ok_cnt), _OK_CLR if ok_cnt > 0 else _MUTED)
        _row("NOK", str(nok_cnt), _NOK_CLR if nok_cnt > 0 else _MUTED)

        ratio = streak / threshold if threshold > 0 else 0
        streak_c = (_NOK_CLR if ratio >= 0.8 else ("#d97706" if ratio >= 0.5 else _MUTED))
        _row("Racha NOK", f"{streak} / {threshold}", streak_c)

        ultimo = self._result_val[1].text()
        if ultimo and ultimo != "—":
            _row("Último estado", ultimo)

        c_txt = self._center_val[1].text()
        if c_txt and c_txt != "—":
            sep = _QLabel("─" * 26)
            sep.setStyleSheet(f"color:{_BORDER};font-size:9px;")
            lay.addWidget(sep)
            c_lbl = _QLabel("Centrado")
            c_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
            c_val = _QLabel(c_txt)
            c_val.setStyleSheet(f"color:{_TEXT};font-weight:700;font-size:11px;")
            c_val.setWordWrap(True)
            lay.addWidget(c_lbl)
            lay.addWidget(c_val)

        dlg.exec()

    # ------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        self._feedback(self.start_btn, "▶  INICIAR", "#1a6b3a", "● Iniciando...")
        if self._scanner.start():
            self._run_start_time = time.monotonic()
        else:
            QMessageBox.warning(self, "Iniciar", f"No se pudo iniciar {self._id}.")

    def _on_stop(self) -> None:
        self._feedback(self.stop_btn, "■  DETENER", "#7f1d1d", "● Deteniendo...")
        self._scanner.stop()
        self._run_start_time = None

    def _on_reset(self) -> None:
        if self._scanner.reset():
            self._feedback(self.reset_btn, "↺  RESET FALLA", "#7c2d12", "● Reseteando...")
        else:
            QMessageBox.information(self, "Reset", "Solo disponible en estado PARADO.")

    def _on_simulate_stop(self) -> None:
        self._scanner.inject_machine_stop("SIMULACION MANUAL")

    def _feedback(self, btn: QPushButton, label: str, flash_bg: str, badge_txt: str) -> None:
        """Confirma visualmente que el botón fue recibido por el sistema."""
        # 1 — badge de estado muestra transición inmediata
        self.state_badge.setText(badge_txt)
        self.state_badge.setStyleSheet(
            f"background:{flash_bg};color:#f0f6fc;border-radius:6px;"
            "padding:10px 14px;font-size:16px;font-weight:700;letter-spacing:1px;"
        )
        # 2 — el badge se actualiza solo en max 200 ms por el timer de status

    def _on_model_changed(self, display_name: str) -> None:
        from src.utils.model_names import to_internal
        internal = to_internal(display_name)
        if internal:
            self._scanner.set_model(internal)
            _num = self._id.split("_")[-1]
            self._title_lbl.setText(f"SCANNER {_num}   ·   {display_name}")

    # ------------------------------------------------------------------
    # Callbacks del controller (threads → señales → hilo principal)
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: ScannerState, mode: OperationMode) -> None:
        pass  # el badge de estado se actualiza por el timer de refresh_status

    def _on_result(self, result: InspectionResult, streak: int) -> None:
        if result.overlay is None:
            return
        if result.machine_stop:
            until_ms = int(time.monotonic() * 1000) + _OVERLAY_HOLD_FAULT_MS
            overlay = result.overlay.copy()
            self._sig_overlay.emit(overlay, until_ms)
            # Alerta a pantalla completa con el frame del error
            from src.utils.model_names import to_display as _td
            _num  = self._id.split("_")[-1]
            model = self._system.io.scanner_config(self._id).get("model", "")
            label = f"SCANNER {_num}   ·   {_td(model) if model else '—'}"
            reason = _stop_reason(result)
            self._sig_stop_alert.emit(overlay, label, reason)
        elif result.status == "NOK":
            until_ms = int(time.monotonic() * 1000) + _OVERLAY_HOLD_MS
            self._sig_overlay.emit(result.overlay.copy(), until_ms)

    def _set_overlay(self, overlay: np.ndarray, until_ms: int) -> None:
        self._last_overlay     = overlay
        self._overlay_until_ms = until_ms

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_buttons(self, state: ScannerState) -> None:
        is_stopped = (state == ScannerState.STOPPED)
        self.reset_btn.setVisible(is_stopped)
        self.start_btn.setEnabled(state == ScannerState.IDLE)
        self.stop_btn.setEnabled(state in (ScannerState.RUNNING, ScannerState.FAULT))

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


# ------------------------------------------------------------------
# Helper: razón de parada legible desde InspectionResult
# ------------------------------------------------------------------

def _stop_reason(result: InspectionResult) -> str:
    if getattr(result, "tilt_warn", False):
        return "Chapa inclinada"
    if getattr(result, "pattern_alignment_warn", False):
        return "Patrón desalineado"
    missing = getattr(result.report, "missing", 0)
    return f"{missing} agujero{'s' if missing != 1 else ''} faltante{'s' if missing != 1 else ''} persistente{'s' if missing != 1 else ''}"


# ------------------------------------------------------------------
# Diálogo de alerta de parada — ocupa toda la ventana
# ------------------------------------------------------------------

class MachineStopDialog(QDialog):
    """
    Modal a pantalla completa que bloquea la UI hasta que el operario
    presiona ACEPTAR. Muestra el frame con el defecto detectado.
    """

    def __init__(
        self,
        overlay: np.ndarray,
        scanner_label: str,
        reason: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("DETENCIÓN DE MÁQUINA")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self._overlay = overlay
        self._scanner_label = scanner_label
        self._reason = reason
        self._build_ui()
        # Maximizar para ocupar toda la pantalla
        self.showMaximized()

    def _build_ui(self) -> None:
        self.setStyleSheet("background:#120607;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Encabezado orientado al operario: alarma + scanner bien dominante.
        top = QFrame()
        top.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #7f1d1d, stop:0.6 #991b1b, stop:1 #450a0a);"
            "border-bottom:3px solid #fecaca;"
        )
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(36, 22, 36, 22)
        top_lay.setSpacing(10)

        alarm_lbl = QLabel("DETENCION DE MAQUINA")
        alarm_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        alarm_lbl.setStyleSheet(
            "color:#fff1f2;font-size:24px;font-weight:800;"
            "letter-spacing:3px;background:transparent;"
        )
        top_lay.addWidget(alarm_lbl)

        scanner_lbl = QLabel(self._scanner_label)
        scanner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scanner_lbl.setStyleSheet(
            "color:#ffffff;font-size:42px;font-weight:900;"
            "letter-spacing:2px;background:#ffffff12;"
            "border:2px solid #fecaca;border-radius:14px;padding:14px 20px;"
        )
        top_lay.addWidget(scanner_lbl)

        instruction_lbl = QLabel("REVISAR ESTA ESTACION ANTES DE REANUDAR LA LINEA")
        instruction_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instruction_lbl.setStyleSheet(
            "color:#fecaca;font-size:17px;font-weight:700;"
            "letter-spacing:1px;background:transparent;"
        )
        top_lay.addWidget(instruction_lbl)
        root.addWidget(top)

        # ── Imagen del frame con el defecto — ocupa el máximo espacio ─
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet(
            "background:#000000;border-top:1px solid #2b0b0b;border-bottom:1px solid #2b0b0b;"
        )
        self._img_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._img_lbl, stretch=1)

        footer = QWidget()
        footer.setFixedHeight(96)
        footer.setStyleSheet("background:#1a0000;border-top:2px solid #7f1d1d;")
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(26, 14, 18, 14)
        foot_lay.setSpacing(16)

        reason_box = QFrame()
        reason_box.setStyleSheet(
            "background:#2a0d10;border:1px solid #7f1d1d;border-radius:12px;"
        )
        reason_lay = QVBoxLayout(reason_box)
        reason_lay.setContentsMargins(18, 10, 18, 10)
        reason_lay.setSpacing(4)

        reason_title = QLabel("MOTIVO DE LA DETENCION")
        reason_title.setStyleSheet(
            "color:#fca5a5;font-size:12px;font-weight:800;letter-spacing:2px;background:transparent;"
        )
        reason_lbl = QLabel(self._reason.upper())
        reason_lbl.setStyleSheet(
            "color:#ffffff;font-size:22px;font-weight:800;background:transparent;"
        )
        reason_lay.addWidget(reason_title)
        reason_lay.addWidget(reason_lbl)
        foot_lay.addWidget(reason_box, stretch=1)

        btn = QPushButton("CONFIRMAR Y CONTINUAR")
        btn.setFixedHeight(68)
        btn.setMinimumWidth(280)
        btn.setStyleSheet(
            "QPushButton { background:#dc2626;color:#ffffff;"
            "font-size:22px;font-weight:900;letter-spacing:1px;"
            "border:none;border-radius:12px;padding:0 20px; }"
            "QPushButton:hover { background:#ef4444; }"
            "QPushButton:pressed { background:#991b1b; }"
        )
        btn.clicked.connect(self.accept)
        foot_lay.addWidget(btn)

        root.addWidget(footer)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_image()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_image()

    def _update_image(self) -> None:
        if self._overlay is None or self._img_lbl is None:
            return
        rect = self._img_lbl.contentsRect()
        w, h = max(1, rect.width() - 8), max(1, rect.height() - 8)
        pix = _bgr_to_pixmap(self._overlay, w, h)
        self._img_lbl.setPixmap(pix)


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

class OperatorWindow(QMainWindow):
    def __init__(self, system: InspectionSystem) -> None:
        super().__init__()
        self._system         = system
        self._service_win    = None
        self._metrics_win    = None
        self._tolerance_win  = None
        self._stop_alert_dlg: Optional[MachineStopDialog] = None
        self._errors_win = None
        self.setWindowTitle("DEFYVISION")
        _ico = _ROOT / "assets" / "defyvision_logo.ico"
        _jpg = _ROOT / "logos" / "logo_ventana.jpg"
        _win_pix = QPixmap(str(_ico)) if _ico.exists() else QPixmap(str(_jpg))
        if not _win_pix.isNull():
            self.setWindowIcon(QIcon(_win_pix))
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
        central.setStyleSheet(f"background:{_BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background:{_BORDER}; width:2px; }}"
        )
        self._panels: dict[str, ScannerPanel] = {}

        for sid in self._system.scanner_ids():
            panel = ScannerPanel(sid, self._system)
            panel._sig_stop_alert.connect(self._show_stop_alert)
            frame = QFrame()
            frame.setStyleSheet(
                f"QFrame {{ background:{_PANEL};border-radius:8px;"
                f"border:1px solid {_BORDER}; }}"
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

        def _hbtn(label, bg, fg, slot):
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border-radius:5px;"
                f"font-size:10px; padding:0 8px; border:none; }}"
                f"QPushButton:hover {{ background:{bg}dd; }}"
            )
            b.clicked.connect(slot)
            return b

        metrics_btn   = _hbtn("Métricas",    "#1e40af", "#ffffff", self._open_metrics)
        errors_btn    = _hbtn("Grabación",   "#7f1d1d", "#fca5a5", self._open_errors)
        tolerance_btn = _hbtn("Tolerancias", "#065f46", "#6ee7b7", self._open_tolerances)
        service_btn   = _hbtn("Servicio",    "#334155", "#94a3b8", self._open_service)

        btn_row.addWidget(metrics_btn)
        btn_row.addWidget(errors_btn)
        btn_row.addWidget(tolerance_btn)
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

    def _open_errors(self) -> None:
        """Abre el visor de frames para el operario (paradas + OK recientes)."""
        from src.ui.frame_viewer import OperatorFrameViewer
        if self._errors_win is None or not self._errors_win.isVisible():
            self._errors_win = OperatorFrameViewer(self._system, parent=None)
        self._errors_win.show()
        self._errors_win.raise_()
        self._errors_win.activateWindow()
        try:
            self._errors_win.reload()
        except Exception:
            pass

    def _show_stop_alert(self, overlay: np.ndarray, label: str, reason: str) -> None:
        """Muestra el diálogo de parada a pantalla completa. Solo uno a la vez."""
        if self._stop_alert_dlg is not None and self._stop_alert_dlg.isVisible():
            return
        self._stop_alert_dlg = MachineStopDialog(overlay, label, reason, parent=self)
        self._stop_alert_dlg.exec()
        self._stop_alert_dlg = None

    def _open_metrics(self) -> None:
        from src.ui.metrics_window import MetricsWindow
        if self._metrics_win is None or not self._metrics_win.isVisible():
            self._metrics_win = MetricsWindow(self._system)
        self._metrics_win.show()
        self._metrics_win.raise_()
        self._metrics_win.activateWindow()

    def _open_tolerances(self) -> None:
        from src.ui.tolerance_window import ToleranceWindow
        if self._tolerance_win is None or not self._tolerance_win.isVisible():
            self._tolerance_win = ToleranceWindow(self._system)
        self._tolerance_win.show()
        self._tolerance_win.raise_()
        self._tolerance_win.activateWindow()

    def _open_service(self) -> None:
        from src.ui.login_dialog import LoginDialog, service_login_enabled
        from src.ui.service import ServiceWindow
        from PyQt6.QtWidgets import QDialog

        if service_login_enabled():
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
            if self._tolerance_win is not None and self._tolerance_win.isVisible():
                self._tolerance_win.close()
            if self._errors_win is not None and self._errors_win.isVisible():
                self._errors_win.close()
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
    # Usar el mismo .ico embebido en el exe para que barra de título y barra de
    # tareas muestren el mismo ícono. Fallback al .jpg si el .ico no existe.
    _ico = _ROOT / "assets" / "defyvision_logo.ico"
    _jpg = _ROOT / "logos" / "logo_ventana.jpg"
    icon_pix = QPixmap(str(_ico)) if _ico.exists() else QPixmap(str(_jpg))
    if not icon_pix.isNull():
        app.setWindowIcon(QIcon(icon_pix))
    win = OperatorWindow(system)
    win.showMaximized()
    win.raise_()
    win.activateWindow()
    app.exec()
