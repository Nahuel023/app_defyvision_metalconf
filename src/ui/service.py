"""
Interfaz de servicio/calibración (PyQt6).

4 pestañas:
  PLC I/O       - tabla de señales en tiempo real, toggle de salidas
  Sistema       - métricas de sesión por scanner + estado PLC
  Logs          - visor de logs Python en tiempo real
  Configuración - visualización read-only de archivos YAML

Se lanza tras autenticación con LoginDialog.
Acepta un InspectionSystem existente (desde OperatorWindow) o crea uno propio.
"""

import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap, QImage, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.utils import camera_config
from src.utils.model_names import DISPLAY_NAMES, to_display, to_internal
from src.utils.state import OperationMode, ScannerState

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent

# Ancho fijo de cada ala del header - igualar ambos lados centra el título
_HEADER_WING_W = 310

# ------------------------------------------------------------------
# Paleta
# ------------------------------------------------------------------
_DARK   = "#0f172a"
_PANEL  = "#1e293b"
_BORDER = "#334155"
_TEXT   = "#f1f5f9"
_MUTED  = "#94a3b8"
_ACCENT = "#38bdf8"
_OK     = "#4ade80"
_NOK    = "#f87171"
_WARN   = "#fbbf24"


# ==================================================================
# Barra de salud del sistema
# ==================================================================

class _HealthChip(QLabel):
    """Chip pill colorizado que muestra el estado de un componente."""

    _STYLES: dict[str, tuple] = {
        "ok":      ("#22c55e", "#052e16", "#16a34a"),   # fg, bg, border
        "warn":    ("#fbbf24", "#1c1507", "#b45309"),
        "error":   ("#f87171", "#1f0606", "#dc2626"),
        "neutral": (_MUTED,   "#0d1929", _BORDER),
    }

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._base_label = label
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(26)
        self.setMinimumWidth(90)
        self.set_state("—", "neutral")

    def set_state(self, detail: str, kind: str = "neutral") -> None:
        fg, bg, border = self._STYLES.get(kind, self._STYLES["neutral"])
        dot = "●" if kind in ("ok", "error") else ("◑" if kind == "warn" else "○")
        self.setText(f"{dot}  {self._base_label}  {detail}")
        self.setStyleSheet(
            f"color:{fg};background:{bg};border:1px solid {border};"
            "border-radius:12px;padding:0 12px;"
            "font-size:11px;font-weight:700;letter-spacing:0.3px;"
        )


class _HealthBar(QWidget):
    """Barra horizontal con chips de estado: PLC · Scanners · Cámaras IP."""

    def __init__(self, system: "InspectionSystem",
                 cam_tab_ref: "list[CameraCalibTab | None]",
                 parent=None) -> None:
        super().__init__(parent)
        self._system   = system
        self._cam_ref  = cam_tab_ref   # mutable list[1] → se rellena después de construir el tab
        self.setFixedHeight(38)
        self.setStyleSheet(
            f"background:{_PANEL};border-radius:8px;"
            f"border:1px solid {_BORDER};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)

        self._plc_chip = _HealthChip("PLC", self)
        lay.addWidget(self._plc_chip)

        self._scan_chips: dict[str, _HealthChip] = {}
        for sid in system.scanner_ids():
            chip = _HealthChip(sid.replace("scanner_", "Scnr "), self)
            self._scan_chips[sid] = chip
            lay.addWidget(chip)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color:{_BORDER};")
        lay.addWidget(sep)

        self._ip_chips: list[_HealthChip] = []
        for i in range(2):
            chip = _HealthChip(f"IP Cám {i+1}", self)
            self._ip_chips.append(chip)
            lay.addWidget(chip)

        lay.addStretch()

    def refresh(self) -> None:
        # PLC
        plc_ok = self._system.plc.connected
        self._plc_chip.set_state(
            "Conectado" if plc_ok else "Desconectado",
            "ok" if plc_ok else "error",
        )

        # Scanners
        _state_kind = {
            "RUNNING": "ok", "IDLE": "neutral",
            "FAULT":   "error", "STOPPED": "warn",
        }
        for sid, chip in self._scan_chips.items():
            try:
                s     = self._system.scanner(sid).get_status()
                state = s["state"].value.upper()
                kind  = _state_kind.get(state, "neutral")
                chip.set_state(state, kind)
            except Exception:
                chip.set_state("—", "neutral")

        # Cámaras IP
        cam_tab = self._cam_ref[0] if self._cam_ref else None
        for i, chip in enumerate(self._ip_chips):
            if cam_tab is None:
                chip.set_state("—", "neutral")
                continue
            fps = cam_tab._ip_fps_value[i] if hasattr(cam_tab, "_ip_fps_value") else 0
            connected = (
                cam_tab._ip_workers[i] is not None or
                cam_tab._ip_caps[i] is not None
            ) if hasattr(cam_tab, "_ip_workers") else False
            retry_count = cam_tab._ip_retry_counts[i] if hasattr(cam_tab, "_ip_retry_counts") else 0

            if connected and fps > 0:
                chip.set_state(f"{fps:.0f} fps", "ok")
            elif connected:
                chip.set_state("Conectando", "warn")
            elif retry_count > 0:
                chip.set_state(f"Reintento {retry_count}", "warn")
            else:
                chip.set_state("Sin señal", "neutral")


# ==================================================================
# Qt logging handler
# ==================================================================

class _LogEmitter(QObject):
    record = pyqtSignal(str, int)   # (formatted_message, levelno)


class QtLogHandler(logging.Handler):
    """Reenvía registros al widget de logs mediante señal Qt (thread-safe)."""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _LogEmitter()
        self.signal   = self._emitter.record
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._emitter.record.emit(self.format(record), record.levelno)
        except Exception:
            self.handleError(record)


# ==================================================================
# Tab 1: PLC I/O
# ==================================================================

class PLCIOTab(QWidget):
    """Tabla de señales PLC con lectura en vivo y toggle de salidas."""

    _COLS = ["Scanner", "Señal", "Tipo", "Valor", "Acción"]

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system  = system
        self._signals = sorted(system.io.signals().items())
        self._value_items: dict[str, QTableWidgetItem] = {}
        self._last_connected: bool | None = None
        self._last_vals: dict[str, object] = {}
        self._build_ui()
        self._populate_table()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        lbl = QLabel("Estado de señales PLC en tiempo real")
        lbl.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        top.addWidget(lbl)
        top.addStretch()
        self._plc_status = QLabel("PLC: -")
        self._plc_status.setStyleSheet(f"color:{_MUTED};font-size:11px;font-weight:600;")
        top.addWidget(self._plc_status)
        root.addLayout(top)

        self._table = QTableWidget(0, len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(4, 90)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background:{_PANEL}; color:{_TEXT};
                gridline-color:{_BORDER}; border:1px solid {_BORDER};
                border-radius:6px; alternate-background-color:#243040;
            }}
            QHeaderView::section {{
                background:{_DARK}; color:{_MUTED};
                border:none; padding:4px 8px; font-size:11px;
            }}
        """)
        root.addWidget(self._table)

    def _populate_table(self) -> None:
        self._table.setRowCount(len(self._signals))
        self._value_items.clear()

        for row, (name, (sig_type, _addr)) in enumerate(self._signals):
            scanner_id, signal_name = name.split(".", 1)
            for col, text in enumerate([scanner_id, signal_name, sig_type]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

            val_item = QTableWidgetItem("-")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, val_item)
            self._value_items[name] = val_item

            if sig_type == "output":
                btn = QPushButton("Toggle")
                btn.setFixedHeight(24)
                btn.setStyleSheet(
                    "background:#1e40af;color:white;border-radius:4px;"
                    "font-size:10px;padding:0 8px;border:none;"
                )
                btn.clicked.connect(lambda _, n=name: self._toggle_output(n))
                self._table.setCellWidget(row, 4, btn)

            self._table.setRowHeight(row, 28)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        connected = self._system.plc.connected
        if connected != self._last_connected:
            self._last_connected = connected
            self._plc_status.setText("PLC: Conectado" if connected else "PLC: Desconectado")
            self._plc_status.setStyleSheet(
                f"color:{_OK if connected else _NOK};font-size:11px;font-weight:600;"
            )

        for name, (sig_type, _addr) in self._signals:
            item = self._value_items.get(name)
            if item is None:
                continue
            val = self._system.io.read(name)
            if name in self._last_vals and self._last_vals[name] == val:
                continue
            self._last_vals[name] = val
            if val is None:
                item.setText("-")
                item.setForeground(QBrush(QColor(_MUTED)))
            else:
                item.setText("ON" if val else "OFF")
                color = (_OK if sig_type == "output" else _ACCENT) if val else _MUTED
                item.setForeground(QBrush(QColor(color)))

    def _toggle_output(self, name: str) -> None:
        current = self._system.io.read(name)
        new_val = not bool(current)
        self._system.io.write(name, new_val)
        logger.info(f"[Servicio] Toggle {name} -> {'ON' if new_val else 'OFF'}")


# ==================================================================
# Tab: Diagnóstico HW - X0-X15 / Y0-Y15
# ==================================================================

class PLCDiagTab(QWidget):
    """Vista de bajo nivel: 16 entradas X (LEDs) y 16 salidas Y (LEDs + toggle)."""

    _COUNT = 16

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._plc = system.plc

        self._x_name: dict[int, str] = {}
        self._y_name: dict[int, str] = {}
        for full, (t, off) in system.io.signals().items():
            short = full.split(".", 1)[1]
            (self._x_name if t == "input" else self._y_name)[off] = short

        self._x_leds: list[QLabel] = []
        self._y_leds: list[QLabel] = []
        self._y_btns: list[QPushButton] = []
        self._y_vals: list[bool] = [False] * self._COUNT
        self._x_cache: list[object] = [None] * self._COUNT
        self._y_cache: list[object] = [None] * self._COUNT
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)
        lay.addWidget(self._x_group())
        lay.addWidget(self._y_group())

    def _x_group(self) -> QGroupBox:
        grp = QGroupBox("Entradas  X  (solo lectura)")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setSpacing(4)
        lay.setContentsMargins(10, 8, 10, 8)
        for i in range(self._COUNT):
            led = self._make_led()
            self._x_leds.append(led)
            lay.addLayout(self._sig_row(led, f"X{oct(i)[2:]}", self._x_name.get(i, "")))
        lay.addStretch()
        return grp

    def _y_group(self) -> QGroupBox:
        grp = QGroupBox("Salidas  Y  (control)")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setSpacing(4)
        lay.setContentsMargins(10, 8, 10, 8)
        for i in range(self._COUNT):
            led = self._make_led()
            self._y_leds.append(led)
            btn = QPushButton("OFF")
            btn.setFixedSize(54, 22)
            if self._y_name.get(i) == "solenoid":
                btn.setEnabled(False)
                btn.setText("LOCK")
                btn.setToolTip("Solenoide bloqueado por seguridad")
                btn.setStyleSheet(
                    "background:#111827;color:#4b5563;border-radius:4px;"
                    "font-size:10px;font-weight:700;border:1px solid #1f2937;"
                )
            else:
                btn.setStyleSheet(
                    "background:#374151;color:white;border-radius:4px;"
                    "font-size:10px;font-weight:700;border:none;"
                )
                btn.clicked.connect(lambda _, idx=i: self._toggle(idx))
            self._y_btns.append(btn)
            lay.addLayout(self._sig_row(led, f"Y{oct(i)[2:]}", self._y_name.get(i, ""), btn))
        lay.addStretch()
        return grp

    def _sig_row(self, led: QLabel, tag: str, sem: str,
                 extra: QPushButton | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(led)
        if sem:
            lbl = QLabel(
                f"{tag}  <span style='color:{_MUTED};font-size:10px;'>{sem}</span>"
            )
            lbl.setTextFormat(Qt.TextFormat.RichText)
        else:
            lbl = QLabel(tag)
        lbl.setStyleSheet(f"color:{_TEXT};font-size:11px;")
        row.addWidget(lbl, stretch=1)
        if extra:
            row.addWidget(extra)
        return row

    def _make_led(self) -> QLabel:
        w = QLabel()
        w.setFixedSize(14, 14)
        w.setStyleSheet(f"background:{_BORDER};border-radius:7px;")
        return w

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        x_vals = self._plc.read_inputs_batch(0, self._COUNT)
        if x_vals:
            for i, v in enumerate(x_vals):
                if v == self._x_cache[i]:
                    continue
                self._x_cache[i] = v
                c = "#22c55e" if v else _BORDER
                self._x_leds[i].setStyleSheet(f"background:{c};border-radius:7px;")

        y_vals = self._plc.read_coils_batch(0, self._COUNT)
        if y_vals:
            for i, v in enumerate(y_vals):
                if v == self._y_cache[i]:
                    continue
                self._y_cache[i] = v
                self._y_vals[i] = v
                c = "#f97316" if v else _BORDER
                self._y_leds[i].setStyleSheet(f"background:{c};border-radius:7px;")
                if self._y_name.get(i) == "solenoid":
                    continue  # botón bloqueado, no actualizar estilo
                self._y_btns[i].setText("ON" if v else "OFF")
                self._y_btns[i].setStyleSheet(
                    f"background:{'#c2410c' if v else '#374151'};"
                    "color:white;border-radius:4px;font-size:10px;font-weight:700;border:none;"
                )

    def _toggle(self, idx: int) -> None:
        if self._y_name.get(idx) == "solenoid":
            logger.warning(f"[SAFETY] Toggle bloqueado: Y{idx} es solenoide")
            return
        new_val = not self._y_vals[idx]
        if self._y_name.get(idx) == "solenoid" and new_val:
            return
        self._plc.write_coil(idx, new_val)
        logger.info(f"[Diagnóstico] Toggle Y{idx} -> {'ON' if new_val else 'OFF'}")


# ==================================================================
# Tab: Prueba de Salidas PLC
# ==================================================================

class PLCOutputTestTab(QWidget):
    """Botones de prueba visual para todas las salidas PLC por scanner."""

    _OUTPUTS = [
        ("light_blue",   "Azul",      "#3b82f6", "#1e3a5f"),
        ("light_green",  "Verde",     "#22c55e", "#14532d"),
        ("light_yellow", "Amarillo",  "#fbbf24", "#78350f"),
        ("light_red",    "Roja",      "#ef4444", "#7f1d1d"),
        ("solenoid",     "Solenoide", "#a855f7", "#4c1d95"),
        ("backlight",    "Backlight", "#cbd5e1", "#334155"),
    ]

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._btns: dict[str, dict[str, QPushButton]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{_DARK}; }}")

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        title = QLabel("Prueba de salidas PLC - activar cada salida manualmente para verificar")
        title.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        warn = QLabel(
            "Precaución: los cambios aquí escriben directamente al PLC "
            "sin pasar por la FSM del scanner."
        )
        warn.setStyleSheet(f"color:{_WARN};font-size:11px;")
        lay.addWidget(warn)

        for sid in self._system.scanner_ids():
            grp = QGroupBox(sid.replace("_", " ").upper())
            grp.setStyleSheet(self._grp_style())
            grp_lay = QVBoxLayout(grp)
            grp_lay.setContentsMargins(14, 16, 14, 12)
            grp_lay.setSpacing(10)

            btn_row = QHBoxLayout()
            btn_row.setSpacing(10)

            btns: dict[str, QPushButton] = {}
            for sig_key, label, _col_on, col_off in self._OUTPUTS:
                btn = QPushButton(label)
                btn.setFixedSize(110, 56)
                if sig_key == "solenoid":
                    btn.setEnabled(False)
                    btn.setToolTip("Solenoide bloqueado por seguridad\n(activación por software deshabilitada)")
                    btn.setStyleSheet(
                        "background:#111827;color:#4b5563;border-radius:8px;"
                        "font-size:12px;font-weight:700;border:1px solid #1f2937;"
                    )
                else:
                    btn.setStyleSheet(
                        f"background:{col_off};color:#94a3b8;border-radius:8px;"
                        "font-size:12px;font-weight:700;border:none;"
                    )
                    btn.clicked.connect(lambda _, s=sid, k=sig_key: self._toggle(s, k))
                btn_row.addWidget(btn)
                btns[sig_key] = btn

            btn_row.addStretch()

            off_btn = QPushButton("Todo OFF")
            off_btn.setFixedSize(90, 56)
            off_btn.setStyleSheet(
                f"background:#1e293b;color:{_MUTED};border-radius:8px;"
                "font-size:11px;font-weight:700;border:1px solid #475569;"
            )
            off_btn.clicked.connect(lambda _, s=sid: self._all_off(s))
            btn_row.addWidget(off_btn)

            grp_lay.addLayout(btn_row)
            lay.addWidget(grp)
            self._btns[sid] = btns

        lay.addStretch()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def refresh(self) -> None:
        _col_map = {k: (on, off) for k, _, on, off in self._OUTPUTS}
        _labels  = {k: lbl for k, lbl, _, _ in self._OUTPUTS}
        for sid, btns in self._btns.items():
            for sig_key, btn in btns.items():
                if sig_key == "solenoid":
                    continue  # bloqueado por seguridad, no actualizar estilo
                val = self._system.io.read(f"{sid}.{sig_key}")
                col_on, col_off = _col_map[sig_key]
                label = _labels[sig_key]
                if val:
                    btn.setStyleSheet(
                        f"background:{col_on};color:white;border-radius:8px;"
                        "font-size:12px;font-weight:700;border:none;"
                    )
                    btn.setText(f"{label}\nON")
                else:
                    btn.setStyleSheet(
                        f"background:{col_off};color:#94a3b8;border-radius:8px;"
                        "font-size:12px;font-weight:700;border:none;"
                    )
                    btn.setText(label)

    def _toggle(self, scanner_id: str, sig_key: str) -> None:
        current = self._system.io.read(f"{scanner_id}.{sig_key}")
        new_val = not bool(current)
        self._system.io.write(f"{scanner_id}.{sig_key}", new_val)
        logger.info(f"[PruebaS] {scanner_id}.{sig_key} -> {'ON' if new_val else 'OFF'}")

    def _all_off(self, scanner_id: str) -> None:
        for sig_key in self._btns.get(scanner_id, {}):
            self._system.io.write(f"{scanner_id}.{sig_key}", False)
        logger.info(f"[PruebaS] {scanner_id} - Todo OFF")

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )


# ==================================================================
# Tab 2: Sistema
# ==================================================================

class SystemTab(QWidget):
    """Métricas de sesión por scanner y estado general del sistema."""

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._scanner_labels: dict[str, dict[str, QLabel]] = {}
        self._scanner_btns: dict[str, dict[str, QPushButton]] = {}
        self._plc_ip_lbl: Optional[QLabel]   = None
        self._plc_conn_lbl: Optional[QLabel] = None
        self._last_plc_connected: bool | None = None
        self._last_scanner_states: dict[str, dict] = {}
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{_DARK}; }}")

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        # PLC group
        plc_group = QGroupBox("PLC")
        plc_group.setStyleSheet(self._group_style())
        plc_lay = QHBoxLayout(plc_group)
        plc_lay.setSpacing(10)

        ip_w, self._plc_ip_lbl   = self._kv("IP / Puerto", "-")
        conn_w, self._plc_conn_lbl = self._kv("Estado",     "-")
        poll_w, self._plc_poll_lbl = self._kv("Poll interval", "-")
        for w in (ip_w, conn_w, poll_w):
            plc_lay.addWidget(w)
        plc_lay.addStretch()
        lay.addWidget(plc_group)

        # Per-scanner groups
        _FIELDS = [
            ("state",             "Estado"),
            ("mode",              "Modo"),
            ("nok_streak",        "Racha NOK actual"),
            ("max_nok_streak",    "Racha NOK máx."),
            ("total_inspections", "Total inspecciones"),
            ("ok_count",          "OK"),
            ("nok_count",         "NOK"),
            ("session_start",     "Inicio de sesión"),
            ("camera",            "Cámara"),
        ]
        for sid in self._system.scanner_ids():
            group = QGroupBox(sid.replace("_", " ").upper())
            group.setStyleSheet(self._group_style())
            from PyQt6.QtWidgets import QGridLayout
            vbox = QVBoxLayout(group)
            vbox.setSpacing(8)
            vbox.setContentsMargins(10, 14, 10, 10)

            grid = QGridLayout()
            grid.setSpacing(8)
            widgets: dict[str, QLabel] = {}
            for i, (key, label) in enumerate(_FIELDS):
                row, col = divmod(i, 3)
                w, lbl = self._kv(label, "-")
                grid.addWidget(w, row, col)
                widgets[key] = lbl
            vbox.addLayout(grid)

            # Control buttons
            ctrl = QHBoxLayout()
            ctrl.setSpacing(8)
            ctrl.setContentsMargins(0, 4, 0, 0)

            def _btn(text: str, color: str) -> QPushButton:
                b = QPushButton(text)
                b.setFixedHeight(30)
                b.setStyleSheet(
                    f"background:{color};color:white;border-radius:5px;"
                    "font-size:11px;font-weight:700;border:none;padding:0 12px;"
                )
                return b

            start_btn = _btn("Iniciar", "#166534")
            stop_btn  = _btn("Detener", "#7f1d1d")
            reset_btn = _btn("Reset  Reset",    "#1e3a5f")
            sim_btn   = _btn("Forzar Inspección", "#78350f")
            cap_btn   = _btn("Capturar frame",    "#1e3a5f")
            cap_btn.setStyleSheet(
                "background:#0f4c81;color:white;border-radius:5px;"
                "font-size:11px;font-weight:700;border:none;padding:0 12px;"
            )

            start_btn.clicked.connect(lambda _, s=sid: self._system.scanner(s).start())
            stop_btn.clicked.connect( lambda _, s=sid: self._system.scanner(s).stop())
            reset_btn.clicked.connect(lambda _, s=sid: self._system.scanner(s).reset())
            sim_btn.clicked.connect(  lambda _, s=sid: self._system.scanner(s).force_inspect())
            cap_btn.clicked.connect(  lambda _, s=sid: self._capture_frame(s))

            for b in (start_btn, stop_btn, reset_btn, sim_btn, cap_btn):
                ctrl.addWidget(b)
            ctrl.addStretch()
            vbox.addLayout(ctrl)

            lay.addWidget(group)
            self._scanner_labels[sid] = widgets
            self._scanner_btns[sid] = {
                "start": start_btn,
                "stop":  stop_btn,
                "reset": reset_btn,
                "sim":   sim_btn,
                "cap":   cap_btn,
            }

        lay.addStretch()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _kv(self, title: str, value: str) -> tuple[QWidget, QLabel]:
        w = QWidget()
        w.setStyleSheet(
            f"background:{_PANEL};border-radius:6px;border:1px solid {_BORDER};"
        )
        l = QVBoxLayout(w)
        l.setContentsMargins(10, 6, 10, 6)
        l.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:9px;color:{_MUTED};")
        v = QLabel(value)
        v.setStyleSheet(f"font-size:12px;font-weight:700;color:{_TEXT};")
        l.addWidget(t)
        l.addWidget(v)
        w.setMinimumWidth(140)
        return w, v

    def _group_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        plc_cfg   = self._system.io.plc_config
        connected = self._system.plc.connected

        if connected != self._last_plc_connected:
            self._last_plc_connected = connected
            self._plc_ip_lbl.setText(f"{plc_cfg['ip']}:{plc_cfg.get('port', 502)}")
            self._plc_conn_lbl.setText("Conectado" if connected else "Desconectado")
            self._plc_conn_lbl.setStyleSheet(
                f"font-size:12px;font-weight:700;"
                f"color:{_OK if connected else _NOK};"
            )
            self._plc_poll_lbl.setText(f"{plc_cfg.get('poll_interval_ms', 50)} ms")

        _state_colors = {
            ScannerState.IDLE:    _MUTED,
            ScannerState.RUNNING: _OK,
            ScannerState.FAULT:   _NOK,
            ScannerState.STOPPED: "#475569",
            ScannerState.ERROR:   _WARN,
        }

        for sid, wdg in self._scanner_labels.items():
            s   = self._system.scanner(sid).get_status()
            cam = self._system.camera(sid)

            state: ScannerState       = s["state"]
            mode:  OperationMode      = s["mode"]
            start: Optional[datetime] = s.get("session_start")
            cam_running = cam.is_running

            last = self._last_scanner_states.get(sid, {})
            changed = (
                state                        != last.get("state") or
                mode                         != last.get("mode") or
                s["nok_streak"]              != last.get("nok_streak") or
                s.get("max_nok_streak", 0)   != last.get("max_nok_streak") or
                s.get("total_inspections", 0) != last.get("total_inspections") or
                s.get("ok_count", 0)         != last.get("ok_count") or
                s.get("nok_count", 0)        != last.get("nok_count") or
                cam_running                  != last.get("cam_running")
            )
            if not changed:
                continue

            self._last_scanner_states[sid] = {
                "state": state, "mode": mode,
                "nok_streak": s["nok_streak"],
                "max_nok_streak": s.get("max_nok_streak", 0),
                "total_inspections": s.get("total_inspections", 0),
                "ok_count": s.get("ok_count", 0),
                "nok_count": s.get("nok_count", 0),
                "cam_running": cam_running,
            }

            wdg["state"].setText(state.value.upper())
            wdg["state"].setStyleSheet(
                f"font-size:12px;font-weight:700;color:{_state_colors[state]};"
            )
            wdg["mode"].setText(mode.value.upper())
            wdg["nok_streak"].setText(str(s["nok_streak"]))
            wdg["max_nok_streak"].setText(str(s.get("max_nok_streak", 0)))
            wdg["total_inspections"].setText(str(s.get("total_inspections", 0)))
            wdg["ok_count"].setText(str(s.get("ok_count", 0)))
            wdg["nok_count"].setText(str(s.get("nok_count", 0)))
            wdg["session_start"].setText(
                start.strftime("%H:%M:%S") if start else "-"
            )
            wdg["camera"].setText(
                f"#{cam.index} {'activa' if cam_running else 'inactiva'}"
            )

            # Button states
            if sid in self._scanner_btns:
                btns = self._scanner_btns[sid]
                is_idle    = state == ScannerState.IDLE
                is_running = state == ScannerState.RUNNING
                is_fault   = state == ScannerState.FAULT
                btns["start"].setEnabled(is_idle)
                btns["stop"].setEnabled(is_running or is_fault)
                btns["reset"].setEnabled(is_fault)
                btns["sim"].setEnabled(is_running)
                btns["cap"].setEnabled(cam_running)

    def _capture_frame(self, scanner_id: str) -> None:
        import cv2
        from pathlib import Path

        cam = self._system.camera(scanner_id)
        frame = cam.get_frame()
        if frame is None:
            logger.warning(f"[{scanner_id}] no hay frame disponible para capturar")
            return

        out_dir = Path("data/frames")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"ref_{scanner_id}_{ts}.png"
        cv2.imwrite(str(path), frame)
        logger.info(f"[{scanner_id}] frame guardado: {path}")


# ==================================================================
# Tab 3: Logs
# ==================================================================

class LogsTab(QWidget):
    """Visor de logs del sistema Python en tiempo real."""

    _LEVEL_COLORS = {
        logging.DEBUG:    _MUTED,
        logging.INFO:     _TEXT,
        logging.WARNING:  _WARN,
        logging.ERROR:    _NOK,
        logging.CRITICAL: "#ef4444",
    }

    def __init__(self, handler: QtLogHandler, parent=None) -> None:
        super().__init__(parent)
        self._handler = handler
        self._build_ui()
        handler.signal.connect(self._append)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        # Log view (created first so toolbar can reference it)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 9))
        self._log_view.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:6px;padding:6px;"
        )

        # Toolbar
        top = QHBoxLayout()
        title_lbl = QLabel("Logs del sistema")
        title_lbl.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        top.addWidget(title_lbl)
        top.addStretch()

        level_lbl = QLabel("Nivel:")
        level_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(level_lbl)

        self._level_combo = QComboBox()
        self._level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._level_combo.setCurrentText("INFO")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        self._level_combo.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 6px;font-size:11px;"
        )
        top.addWidget(self._level_combo)

        clear_btn = QPushButton("Limpiar")
        clear_btn.setFixedHeight(26)
        clear_btn.setStyleSheet(
            f"background:#475569;color:white;border-radius:4px;"
            "font-size:11px;padding:0 10px;border:none;"
        )
        clear_btn.clicked.connect(self._log_view.clear)
        top.addWidget(clear_btn)

        root.addLayout(top)
        root.addWidget(self._log_view)

    def _append(self, msg: str, levelno: int) -> None:
        min_level = getattr(logging, self._level_combo.currentText(), logging.INFO)
        if levelno < min_level:
            return
        color = self._LEVEL_COLORS.get(levelno, _TEXT)
        safe  = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._log_view.append(f'<span style="color:{color};">{safe}</span>')
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_level_changed(self, level_name: str) -> None:
        self._handler.setLevel(getattr(logging, level_name, logging.INFO))


# ==================================================================
# Tab 4: Configuración
# ==================================================================

class ConfigTab(QWidget):
    """Visualización read-only de archivos YAML de configuración."""

    _FILES = [
        ("Tolerancias", "config/tolerancias.yaml"),
        ("I/O Map",     "config/io_map.yaml"),
        ("App",         "config/app.yaml"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        inner = QTabWidget()
        inner.setStyleSheet(f"""
            QTabWidget::pane {{
                background:{_PANEL};border:1px solid {_BORDER};border-radius:6px;
            }}
            QTabBar::tab {{
                background:{_DARK};color:{_MUTED};
                padding:5px 14px;font-size:11px;border-radius:4px;
            }}
            QTabBar::tab:selected {{ background:{_PANEL};color:{_TEXT}; }}
        """)

        for title, path in self._FILES:
            editor = QTextEdit()
            editor.setReadOnly(True)
            editor.setFont(QFont("Consolas", 9))
            editor.setStyleSheet(f"background:{_DARK};color:{_TEXT};border:none;padding:8px;")
            editor.setPlainText(self._load(path))
            inner.addTab(editor, title)

        root.addWidget(inner)

    @staticmethod
    def _load(path: str) -> str:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            masked = []
            for line in lines:
                if "password" in line.lower() and ":" in line:
                    key, _ = line.split(":", 1)
                    masked.append(f"{key}: ***")
                else:
                    masked.append(line)
            return "\n".join(masked)
        except FileNotFoundError:
            return f"# Archivo no encontrado: {path}"
        except Exception as exc:
            return f"# Error al cargar: {exc}"


# ==================================================================
# Tab 5: Grabación / Análisis / Navegador
# ==================================================================

class _AnalysisWorker(QThread):
    progress  = pyqtSignal(int, int)          # (done, total)
    finished  = pyqtSignal(list)              # list[InspectionResult]
    error     = pyqtSignal(str)
    cancelled = pyqtSignal(int)               # (frames_analizados antes de cancelar)

    def __init__(self, model: str, frame_paths: list, scanner_id: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._model      = model
        self._paths      = frame_paths
        self._scanner_id = scanner_id
        self._cancel     = False

    def cancel(self) -> None:
        """Solicita la cancelación del análisis (thread-safe: flag booleano)."""
        self._cancel = True

    def run(self) -> None:
        from src.utils.config import load_tolerances
        from src.vision.inspector import InspectionSession

        try:
            n = len(self._paths)
            if n == 0:
                self.finished.emit([])
                return

            tols = load_tolerances(self._model, scanner_id=self._scanner_id)
            movement_threshold = float(tols.get("continuous_position_threshold", 0.0))
            session = InspectionSession(
                self._model,
                scanner_id=self._scanner_id,
                movement_threshold=movement_threshold,
                min_interval_sec=0.0,
            )

            results: list = []
            for i, path in enumerate(self._paths):
                if self._cancel:
                    self.cancelled.emit(i)
                    return
                result = session.inspect_path(path)
                if result is not None:
                    results.append(result)
                # Emitir progreso en cada frame para que la UI muestre avance en vivo.
                self.progress.emit(i + 1, n)

            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))



class ZoomableImageView(QWidget):
    """Widget de imagen con zoom (rueda) y pan (drag). API: set_pixmap / clear / fit."""

    def __init__(self, placeholder: str = "Sin imagen", parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._placeholder = placeholder
        self._scale: float = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._drag_start: QPointF | None = None
        self._drag_offset: QPointF | None = None
        self.setMinimumHeight(360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:6px;"
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_pixmap(self, pixmap: QPixmap, auto_fit: bool = True) -> None:
        self._pixmap = pixmap
        if auto_fit:
            self.fit()
        else:
            self.update()

    def current_pixmap(self) -> QPixmap | None:
        return self._pixmap

    def clear(self, placeholder: str | None = None) -> None:
        self._pixmap = None
        if placeholder is not None:
            self._placeholder = placeholder
        self._scale = 1.0
        self._offset = QPointF(0.0, 0.0)
        self.update()

    def fit(self) -> None:
        if self._pixmap is None:
            return
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw == 0 or ph == 0:
            return
        self._scale = min(w / pw, h / ph)
        self._offset = QPointF(
            (w - pw * self._scale) / 2.0,
            (h - ph * self._scale) / 2.0,
        )
        self.update()

    @property
    def zoom_pct(self) -> int:
        return int(round(self._scale * 100))

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pixmap is not None:
            self.fit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(_DARK))

        if self._pixmap is None:
            painter.setPen(QColor(_MUTED))
            painter.setFont(QFont("Segoe UI", 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        pw = self._pixmap.width() * self._scale
        ph = self._pixmap.height() * self._scale
        target = QRectF(self._offset.x(), self._offset.y(), pw, ph)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

        # Zoom % badge
        painter.setPen(QColor(_MUTED))
        painter.setFont(QFont("Segoe UI", 9))
        badge = f"{self.zoom_pct}%"
        painter.drawText(self.rect().adjusted(0, 4, -6, 0),
                         Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, badge)

    def wheelEvent(self, event) -> None:
        if self._pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        new_scale = max(0.05, min(self._scale * factor, 30.0))

        # Zoom toward cursor position
        cursor = QPointF(event.position())
        img_x = (cursor.x() - self._offset.x()) / self._scale
        img_y = (cursor.y() - self._offset.y()) / self._scale
        self._scale = new_scale
        self._offset = QPointF(
            cursor.x() - img_x * self._scale,
            cursor.y() - img_y * self._scale,
        )
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._drag_start = QPointF(event.position())
            self._drag_offset = QPointF(self._offset)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None and self._drag_offset is not None:
            delta = event.position() - self._drag_start
            self._offset = self._drag_offset + delta
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
            self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.fit()


class _MJPEGReader(QThread):
    """Lee un stream MJPEG/HTTP frame a frame y emite cada imagen como np.ndarray.

    cv2.VideoCapture no puede abrir streams MJPEG sobre HTTP en Windows.
    Este hilo usa urllib para leer el stream en crudo y detecta los marcadores
    JPEG (SOI 0xFF 0xD8 ... EOI 0xFF 0xD9) directamente en el flujo de bytes.
    Funciona con cualquier stream MJPEG estándar (Sony, Hikvision, Dahua, etc.).
    """

    frame_ready      = pyqtSignal(object)          # np.ndarray BGR
    frame_ready_meta = pyqtSignal(object, object)  # frame, dict metadata
    error_occurred   = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        parent=None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._url        = url
        self._stop_flag  = False
        self._username   = username or ""
        self._password   = password or ""

    def stop(self) -> None:
        self._stop_flag = True
        self.wait(3000)

    def run(self) -> None:
        import base64
        import urllib.request
        self._stop_flag = False
        try:
            headers = {}
            if self._username and self._password:
                token = base64.b64encode(
                    f"{self._username}:{self._password}".encode("utf-8")
                ).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
            request = urllib.request.Request(self._url, headers=headers)
            req = urllib.request.urlopen(request, timeout=10)
            buf = b""
            while not self._stop_flag:
                chunk = req.read(4096)
                if not chunk:
                    break
                buf += chunk
                # Scan for JPEG SOI ... EOI boundaries
                latest_frame = None
                decoded_count = 0
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start == -1:
                        if len(buf) > 1024 * 1024:
                            buf = buf[-4096:]
                        break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end == -1:
                        buf = buf[start:]
                        break
                    jpg = buf[start : end + 2]
                    buf = buf[end + 2:]
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        latest_frame = frame
                        decoded_count += 1
                if latest_frame is not None:
                    meta = {
                        "monotonic_ts": time.monotonic(),
                        "dropped": max(0, decoded_count - 1),
                    }
                    self.frame_ready.emit(latest_frame)
                    self.frame_ready_meta.emit(latest_frame, meta)
        except Exception as exc:
            if not self._stop_flag:
                self.error_occurred.emit(str(exc))


class _HTTPSnapshotReader(QThread):
    """Polling de URL JPEG/HTTP con conexión persistente (keep-alive).

    Usa http.client directamente para reusar la conexión TCP entre frames,
    eliminando el overhead del handshake TCP en cada captura.
    El intervalo real queda limitado por la latencia de red + tiempo de captura
    de la cámara (típicamente 20-60 ms en LAN → máx ~20-30 fps práctico).
    """

    frame_ready_meta = pyqtSignal(object, object)  # frame, dict metadata
    error_occurred   = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        parent=None,
        username: str | None = None,
        password: str | None = None,
        interval_ms: int = 67,          # 67 ms ≈ 15 fps
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._stop_flag = False
        self._username = username or ""
        self._password = password or ""
        # El preview de diagnóstico se refresca a ~5 fps (timer de 200 ms). Capturar
        # a 30 fps saturaba CPU y el WiFi decodificando JPEG que nunca se mostraban.
        # 150 ms da margen sobre el preview sin desperdiciar red/CPU.
        self._interval_ms = max(50, interval_ms)   # mín 50 ms (20 fps techo)

    def stop(self) -> None:
        self._stop_flag = True
        self.wait(3000)

    def run(self) -> None:
        import base64
        import http.client
        from urllib.parse import urlparse

        self._stop_flag = False
        parsed  = urlparse(self._url)
        host    = parsed.netloc
        path    = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        use_ssl = parsed.scheme.lower() == "https"

        auth_header = ""
        if self._username and self._password:
            token = base64.b64encode(
                f"{self._username}:{self._password}".encode("utf-8")
            ).decode("ascii")
            auth_header = f"Basic {token}"

        conn: http.client.HTTPConnection | None = None

        def _make_conn():
            if use_ssl:
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                return http.client.HTTPSConnection(host, timeout=5, context=ctx)
            return http.client.HTTPConnection(host, timeout=5)

        while not self._stop_flag:
            tick_start = time.monotonic()
            try:
                if conn is None:
                    conn = _make_conn()
                headers = {"Connection": "keep-alive"}
                if auth_header:
                    headers["Authorization"] = auth_header
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                jpg = resp.read()
                if resp.status != 200:
                    conn.close()
                    conn = None
                else:
                    arr   = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        self.frame_ready_meta.emit(
                            frame,
                            {"monotonic_ts": time.monotonic(), "dropped": 0},
                        )
            except Exception as exc:
                # Reconectar en el próximo ciclo
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn = None
                if self._stop_flag:
                    break
                # Emitir error solo si supera varios fallos consecutivos
                # para no interrumpir por un blip de red
                self.msleep(200)
                continue

            remaining = (self._interval_ms / 1000.0) - (time.monotonic() - tick_start)
            if remaining > 0:
                self.msleep(int(remaining * 1000))

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


class RecordingTab(QWidget):
    """Grabación continua de frames, análisis offline y navegador de resultados."""

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system       = system
        self._recording    = False
        self._rec_dir: Optional[Path] = None
        self._frame_paths: list[Path] = []
        self._results: list           = []
        self._current_idx: int        = 0
        self._worker: Optional[_AnalysisWorker] = None
        self._live_ms_detector = None
        self._live_pre: dict | None = None
        self._live_session = None

        # ROI manual calibration state
        self._roi_frame                     = None   # BGR ndarray del frame de referencia
        self._roi_lx:        int            = 0      # borde izquierdo actual
        self._roi_rx:        int            = 0      # borde derecho actual

        # Timer-based analysis state (runs in main thread, no cross-thread signal issues)
        self._ana_running:    bool          = False
        self._ana_frame_idx:  int           = 0
        self._ana_model:      str           = ""
        self._ana_scanner_id: Optional[str] = None
        self._ana_pre:        dict          = {}

        # PNG writes go to a background thread so the main thread stays responsive.
        self._write_executor = None   # concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # QPixmap cache: (idx, overlay_bool) -> QPixmap.  Avoids re-converting BGR->QPixmap
        # on every navigation click. Cleared when a new analysis/load replaces the data.
        self._px_cache: dict = {}
        self._px_cache_max = 24  # keep last ~24 pixmaps (decode JPEG es barato; baja RAM)
        # Overlays comprimidos a JPEG (bytes) por frame analizado. Evita mantener 200
        # arrays BGR de 1920x1080 (~6 MB c/u) en RAM, que saturaban la PC al navegar.
        self._overlay_jpegs: list = []

        # Track last result-card state to skip redundant setStyleSheet calls.
        self._last_card_state: str | None = None

        # Indices of NOK frames for quick navigation.
        self._nok_indices: list[int] = []

        # IP camera live view - MJPEG worker (HTTP) or cv2 fallback (RTSP/USB)
        self._ip_worker: Optional[_MJPEGReader] = None
        self._ip_cap:    Optional[cv2.VideoCapture] = None
        self._ip_timer = QTimer(self)
        self._ip_timer.timeout.connect(self._refresh_ip_camera)

        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._grab_frame)
        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Auto-conectar cámara del scanner seleccionado al abrir la pestaña
        QTimer.singleShot(0, lambda: self._auto_connect_scanner_camera(
            self._scanner_combo.currentText()
        ))
        # Actualizar tope de FPS cada 2s conforme el snapshot loop estabiliza su medición
        self._fps_cap_timer = QTimer(self)
        self._fps_cap_timer.timeout.connect(self._update_fps_cap)
        self._fps_cap_timer.start(2000)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # RecordingTab no crea su propio layout visible — expone dos páginas
        # que ServiceWindow monta dentro del tab "Cámara".
        self._grab_page = self._build_grab_page()
        self._ana_page  = self._build_ana_page()
        self._cal_page  = self._build_cal_page()

        # Signal wiring
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_load.clicked.connect(self._on_load_recording)
        self._btn_analyze.clicked.connect(self._on_analyze)
        self._btn_read_cam.clicked.connect(self._refresh_cam_info)
        self._btn_first.clicked.connect(lambda: self._show_frame(0))
        self._btn_prev10.clicked.connect(lambda: self._show_frame(self._current_idx - 10))
        self._btn_prev.clicked.connect(lambda: self._show_frame(self._current_idx - 1))
        self._btn_next.clicked.connect(lambda: self._show_frame(self._current_idx + 1))
        self._btn_next10.clicked.connect(lambda: self._show_frame(self._current_idx + 10))
        self._btn_last.clicked.connect(lambda: self._show_frame(len(self._frame_paths) - 1))
        self._btn_prev_nok.clicked.connect(self._go_prev_nok)
        self._btn_next_nok.clicked.connect(self._go_next_nok)
        self._btn_fit.clicked.connect(self._img_view.fit)
        self._btn_save_current.clicked.connect(self._save_current_frame)
        self._btn_export.clicked.connect(self._export_range)
        self._spin_from.valueChanged.connect(self._update_export_label)
        self._spin_to.valueChanged.connect(self._update_export_label)
        self._overlay_toggle.toggled.connect(self._on_overlay_toggled)
        self._model_combo.currentTextChanged.connect(self._update_model_chip)

        # ROI section
        self._btn_roi_pick_img.clicked.connect(self._on_roi_pick_image)
        self._btn_roi_pick_dir.clicked.connect(self._on_roi_pick_folder)
        self._btn_lx_left.clicked.connect(lambda: self._roi_move("lx", -1))
        self._btn_lx_right.clicked.connect(lambda: self._roi_move("lx", +1))
        self._btn_rx_left.clicked.connect(lambda: self._roi_move("rx", -1))
        self._btn_rx_right.clicked.connect(lambda: self._roi_move("rx", +1))
        self._btn_roi_save.clicked.connect(self._on_roi_save)
        self._refresh_current_roi_label()

        self._update_nav_state()
        self._sync_model_buttons()   # sincroniza grab + ana después de construir ambas páginas
        self._update_model_chip(self._model_combo.currentText())
        self._set_rec_badge("standby", 0, None)

    def _build_grab_page(self) -> QWidget:
        """Página GRABACIÓN: controles izq + cámara der."""
        w = QWidget()
        w.setStyleSheet(f"background:{_DARK};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        ctrl = QWidget()
        ctrl_lay = QVBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.setSpacing(10)
        ctrl_lay.addWidget(self._build_recording_section())
        ctrl_lay.addStretch()
        lay.addWidget(ctrl)

        self._cam_panel = self._build_ip_camera_section()
        lay.addWidget(self._cam_panel, stretch=1)
        return w

    def _build_ana_page(self) -> QWidget:
        """Página ANÁLISIS: scroll vertical único sobre toda la página."""
        page = QWidget()
        page.setStyleSheet(f"background:{_DARK};")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(10, 10, 10, 18)
        content_lay.setSpacing(8)
        content_lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        analysis_section = self._build_analysis_section()
        analysis_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        content_lay.addWidget(analysis_section)

        _scroll_style = (
            f"QScrollArea {{ border:none; background:{_DARK}; }}"
            f"QScrollBar:vertical {{ background:{_PANEL};width:8px;border-radius:4px; }}"
            f"QScrollBar::handle:vertical {{ background:{_BORDER};border-radius:4px;min-height:30px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        browser_section = self._build_browser_section()
        browser_section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        content_lay.addWidget(browser_section)
        content_lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setStyleSheet(_scroll_style)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        return page

    def _build_cal_page(self) -> QWidget:
        """Página CALIBRACIÓN: sección de ajuste manual de ROI con scroll."""
        page = QWidget()
        page.setStyleSheet(f"background:{_DARK};")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(14, 14, 14, 14)
        content_lay.setSpacing(10)
        content_lay.addWidget(self._build_roi_section())
        content_lay.addStretch()

        _scroll_style = (
            f"QScrollArea {{ background:{_DARK};border:none; }}"
            f"QScrollBar:vertical {{ background:{_DARK};width:8px;border-radius:4px; }}"
            f"QScrollBar::handle:vertical {{ background:#334155;border-radius:4px;min-height:24px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(_scroll_style)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        return page

    def _build_recording_section(self) -> QGroupBox:
        grp = QGroupBox("GRABACIÓN")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(10)

        def _chip(label: str) -> QLabel:
            l = QLabel(label)
            l.setStyleSheet(
                f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
                f"background:{_DARK};border:1px solid {_BORDER};"
                "border-radius:4px;padding:2px 8px;margin-right:4px;"
            )
            return l

        # ── Fila 1: Scanner + modelo ──────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(_chip("SCANNER"))
        self._scanner_combo = self._make_combo(self._system.scanner_ids(), min_w=100)
        self._scanner_combo.currentTextChanged.connect(self._on_scanner_changed)
        self._scanner_combo.currentTextChanged.connect(self._sync_service_scanner_defaults)
        row1.addWidget(self._scanner_combo)
        row1.addSpacing(12)

        # Model toggle buttons
        self._btn_model_esterilla = QPushButton("ESTERILLA")
        self._btn_model_esterilla.setCheckable(True)
        self._btn_model_esterilla.setFixedHeight(34)
        self._btn_model_microperf = QPushButton("MICROPERFORADO")
        self._btn_model_microperf.setCheckable(True)
        self._btn_model_microperf.setFixedHeight(34)

        self._model_btn_group = QButtonGroup(self)
        self._model_btn_group.setExclusive(True)
        self._model_btn_group.addButton(self._btn_model_esterilla, 0)
        self._model_btn_group.addButton(self._btn_model_microperf, 1)

        self._model_combo = QComboBox()
        self._model_combo.addItems(DISPLAY_NAMES)
        self._model_combo.setVisible(False)

        sids = self._system.scanner_ids()
        _initial = "Esterilla"
        if sids:
            _m = self._system.io.scanner_config(sids[0]).get("model", "")
            if _m:
                _initial = to_display(_m)
        self._model_combo.setCurrentText(_initial)

        self._btn_model_esterilla.toggled.connect(
            lambda checked: self._on_model_btn_toggled("Esterilla", checked)
        )
        self._btn_model_microperf.toggled.connect(
            lambda checked: self._on_model_btn_toggled("Microperforado", checked)
        )
        self._sync_model_buttons()

        row1.addWidget(self._btn_model_esterilla)
        row1.addSpacing(4)
        row1.addWidget(self._btn_model_microperf)
        row1.addWidget(self._model_combo)
        row1.addStretch()
        lay.addLayout(row1)

        # ── Fila 2: FPS + análisis en vivo + info cámara ─────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(_chip("FPS"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(10)
        self._fps_spin.setStyleSheet(
            f"QSpinBox {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:4px 22px 4px 6px;font-size:12px;max-width:72px; }"
            f"QSpinBox::up-button {{ subcontrol-origin:border;subcontrol-position:top right;"
            f"width:18px;border-left:1px solid {_BORDER};border-bottom:1px solid {_BORDER};"
            f"border-top-right-radius:5px;background:{_DARK}; }}"
            f"QSpinBox::down-button {{ subcontrol-origin:border;subcontrol-position:bottom right;"
            f"width:18px;border-left:1px solid {_BORDER};border-top:1px solid {_BORDER};"
            f"border-bottom-right-radius:5px;background:{_DARK}; }}"
            f"QSpinBox::up-arrow {{ width:8px;height:8px; }}"
            f"QSpinBox::down-arrow {{ width:8px;height:8px; }}"
        )
        row2.addWidget(self._fps_spin)
        row2.addSpacing(8)
        self._live_chk = QCheckBox("Análisis en vivo")
        self._live_chk.setChecked(False)
        self._live_chk.setStyleSheet(f"color:{_TEXT};font-size:12px;")
        row2.addWidget(self._live_chk)
        row2.addStretch()
        self._btn_read_cam = QPushButton("Actualizar cámara")
        self._btn_read_cam.setFixedHeight(28)
        self._btn_read_cam.setStyleSheet(
            f"QPushButton {{ background:{_PANEL};color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:10px;font-weight:600;padding:0 10px; }}"
            f"QPushButton:hover {{ color:{_TEXT};border-color:#64748b; }}"
        )
        row2.addWidget(self._btn_read_cam)
        self._cam_info_lbl = QLabel("-")
        self._cam_info_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas,monospace;"
        )
        row2.addWidget(self._cam_info_lbl)

        lay.addLayout(row2)

        # ── Action row: buttons + state badge ────────────────────────
        act = QHBoxLayout()
        act.setSpacing(10)

        self._btn_start = self._mk_btn("INICIAR GRABACION", "#15803d", h=42, fs=13)
        self._btn_stop  = self._mk_btn("DETENER",           "#991b1b", h=42, fs=13)
        self._btn_stop.setEnabled(False)
        act.addWidget(self._btn_start)
        act.addWidget(self._btn_stop)
        act.addStretch()

        # State badge - prominent indicator panel
        badge = QFrame()
        badge.setStyleSheet(
            f"QFrame {{ background:{_DARK};border:1px solid {_BORDER};border-radius:10px; }}"
        )
        badge_lay = QHBoxLayout(badge)
        badge_lay.setContentsMargins(20, 10, 24, 10)
        badge_lay.setSpacing(18)

        left_col = QVBoxLayout()
        left_col.setSpacing(2)
        left_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_state_lbl = QLabel("STANDBY")
        self._rec_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_state_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-weight:700;"
            "letter-spacing:3px;background:transparent;"
        )
        self._rec_folder_lbl = QLabel("-")
        self._rec_folder_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_folder_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:9px;font-family:Consolas;background:transparent;"
        )
        left_col.addWidget(self._rec_state_lbl)
        left_col.addWidget(self._rec_folder_lbl)

        self._rec_count_lbl = QLabel("0")
        self._rec_count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_count_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:32px;font-weight:700;"
            "letter-spacing:1px;background:transparent;"
        )
        frames_lbl = QLabel("FRAMES")
        frames_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frames_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:9px;font-weight:700;"
            "letter-spacing:3px;background:transparent;"
        )
        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        right_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self._rec_count_lbl)
        right_col.addWidget(frames_lbl)

        badge_lay.addLayout(left_col)
        badge_lay.addLayout(right_col)
        act.addWidget(badge)

        lay.addLayout(act)
        return grp

    # ------------------------------------------------------------------ ROI section

    def _build_roi_section(self) -> QGroupBox:
        grp = QGroupBox("CALIBRACIÓN ROI")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(8)

        BTN_SS = (
            f"QPushButton {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:11px;padding:0 10px;min-height:28px; }}"
            f"QPushButton:hover {{ border-color:{_ACCENT}; }}"
        )
        ARROW_SS = (
            f"QPushButton {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:14px;font-weight:700;min-width:32px;min-height:32px; }}"
            f"QPushButton:hover {{ border-color:{_ACCENT};color:{_ACCENT}; }}"
            "QPushButton:pressed { background:#0f172a; }"
        )
        CHIP_SS = (
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 8px;"
        )

        # ── Selector de scanner ──────────────────────────────────────
        sc_row = QHBoxLayout()
        sc_row.setSpacing(8)
        sc_chip = QLabel("SCANNER")
        sc_chip.setStyleSheet(CHIP_SS)
        sc_row.addWidget(sc_chip)
        self._roi_scanner_combo = self._make_combo(self._system.scanner_ids(), min_w=120)
        self._roi_scanner_combo.currentTextChanged.connect(self._on_roi_scanner_changed)
        sc_row.addWidget(self._roi_scanner_combo)
        sc_row.addStretch()
        lay.addLayout(sc_row)

        # ── ROI actual (cargada del archivo) ─────────────────────────
        self._roi_current_lbl = QLabel("ROI actual: —")
        self._roi_current_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-family:Consolas,monospace;"
        )
        lay.addWidget(self._roi_current_lbl)

        # ── Fuente ───────────────────────────────────────────────────
        src_row = QHBoxLayout()
        src_row.setSpacing(6)
        self._btn_roi_pick_img = QPushButton("Abrir imagen")
        self._btn_roi_pick_img.setStyleSheet(BTN_SS)
        self._btn_roi_pick_dir = QPushButton("Abrir carpeta")
        self._btn_roi_pick_dir.setStyleSheet(BTN_SS)
        src_row.addWidget(self._btn_roi_pick_img)
        src_row.addWidget(self._btn_roi_pick_dir)
        src_row.addStretch()
        lay.addLayout(src_row)

        # ── Preview ───────────────────────────────────────────────────
        self._roi_preview_lbl = QLabel("Cargar una imagen para comenzar")
        self._roi_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._roi_preview_lbl.setFixedHeight(160)
        self._roi_preview_lbl.setStyleSheet(
            f"background:#0a0f1a;border-radius:6px;border:1px solid {_BORDER};"
            f"color:{_MUTED};font-size:11px;"
        )
        lay.addWidget(self._roi_preview_lbl)

        # ── Controles borde izquierdo ────────────────────────────────
        lx_row = QHBoxLayout()
        lx_row.setSpacing(6)
        lx_chip = QLabel("BORDE IZQ")
        lx_chip.setStyleSheet(CHIP_SS)
        lx_row.addWidget(lx_chip)
        self._btn_lx_left  = QPushButton("◄")
        self._btn_lx_right = QPushButton("►")
        self._btn_lx_left.setStyleSheet(ARROW_SS)
        self._btn_lx_right.setStyleSheet(ARROW_SS)
        lx_row.addWidget(self._btn_lx_left)
        lx_row.addWidget(self._btn_lx_right)
        lx_row.addStretch()
        self._roi_lx_lbl = QLabel("x=—")
        self._roi_lx_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-family:Consolas,monospace;"
        )
        lx_row.addWidget(self._roi_lx_lbl)
        lay.addLayout(lx_row)

        # ── Controles borde derecho ──────────────────────────────────
        rx_row = QHBoxLayout()
        rx_row.setSpacing(6)
        rx_chip = QLabel("BORDE DER")
        rx_chip.setStyleSheet(CHIP_SS)
        rx_row.addWidget(rx_chip)
        self._btn_rx_left  = QPushButton("◄")
        self._btn_rx_right = QPushButton("►")
        self._btn_rx_left.setStyleSheet(ARROW_SS)
        self._btn_rx_right.setStyleSheet(ARROW_SS)
        rx_row.addWidget(self._btn_rx_left)
        rx_row.addWidget(self._btn_rx_right)
        rx_row.addStretch()
        self._roi_rx_lbl = QLabel("x=—")
        self._roi_rx_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-family:Consolas,monospace;"
        )
        rx_row.addWidget(self._roi_rx_lbl)
        lay.addLayout(rx_row)

        # ── Paso + resultado ─────────────────────────────────────────
        bot_row = QHBoxLayout()
        bot_row.setSpacing(8)
        paso_chip = QLabel("PASO px")
        paso_chip.setStyleSheet(CHIP_SS)
        bot_row.addWidget(paso_chip)
        self._spin_roi_step = QSpinBox()
        self._spin_roi_step.setRange(1, 100)
        self._spin_roi_step.setValue(5)
        self._spin_roi_step.setStyleSheet(
            f"QSpinBox {{ background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:4px 22px 4px 6px;font-size:12px;max-width:66px; }"
            f"QSpinBox::up-button {{ subcontrol-origin:border;subcontrol-position:top right;"
            f"width:18px;border-left:1px solid {_BORDER};border-bottom:1px solid {_BORDER};"
            f"border-top-right-radius:5px;background:{_DARK}; }}"
            f"QSpinBox::down-button {{ subcontrol-origin:border;subcontrol-position:bottom right;"
            f"width:18px;border-left:1px solid {_BORDER};border-top:1px solid {_BORDER};"
            f"border-bottom-right-radius:5px;background:{_DARK}; }}"
        )
        bot_row.addWidget(self._spin_roi_step)
        bot_row.addStretch()
        self._roi_result_lbl = QLabel("w=—")
        self._roi_result_lbl.setStyleSheet(
            f"color:{_ACCENT};font-size:11px;font-family:Consolas,monospace;"
        )
        bot_row.addWidget(self._roi_result_lbl)
        lay.addLayout(bot_row)

        # ── Guardar + estado ─────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.setSpacing(10)
        self._btn_roi_save = self._mk_btn("GUARDAR ROI", "#15803d", h=36, fs=12)
        self._btn_roi_save.setEnabled(False)
        save_row.addWidget(self._btn_roi_save, stretch=1)
        lay.addLayout(save_row)

        self._roi_status_lbl = QLabel("")
        self._roi_status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas,monospace;"
        )
        self._roi_status_lbl.setWordWrap(True)
        lay.addWidget(self._roi_status_lbl)

        return grp

    # ------------------------------------------------------------------ ROI handlers

    def _on_roi_scanner_changed(self) -> None:
        """Al cambiar el scanner: actualiza la etiqueta y recarga bordes sobre la imagen actual."""
        self._refresh_current_roi_label()
        if self._roi_frame is None:
            return
        from src.patterns.roi import load_roi
        from src.utils.model_names import to_internal as _to_int
        scanner_id = self._roi_scanner_combo.currentText() or None
        roi = load_roi(_to_int(self._model_combo.currentText()), scanner_id)
        W = self._roi_frame.shape[1]
        if roi:
            self._roi_lx = roi.x
            self._roi_rx = roi.x + roi.w
        else:
            self._roi_lx = 0
            self._roi_rx = W
        self._roi_redraw()

    def _refresh_current_roi_label(self) -> None:
        from src.patterns.roi import load_roi
        from src.utils.model_names import to_internal as _to_int
        scanner_id = self._roi_scanner_combo.currentText() or None if hasattr(self, "_roi_scanner_combo") else None
        model      = self._model_combo.currentText() if hasattr(self, "_model_combo") else ""
        roi = load_roi(_to_int(model), scanner_id)
        if roi:
            self._roi_current_lbl.setText(
                f"ROI actual:  x={roi.x}  y={roi.y}  w={roi.w}  h={roi.h}"
            )
        else:
            self._roi_current_lbl.setText("ROI actual: — (sin ROI guardada)")

    def _roi_load_frame(self, path: Path) -> None:
        import cv2
        img = cv2.imread(str(path))
        if img is None:
            self._roi_status_lbl.setText(f"No se pudo leer: {path.name}")
            return
        self._roi_frame = img
        H, W = img.shape[:2]
        # Inicializar bordes con ROI guardada si existe, o imagen completa
        from src.patterns.roi import load_roi
        from src.utils.model_names import to_internal as _to_int
        scanner_id = self._roi_scanner_combo.currentText() or None
        roi = load_roi(_to_int(self._model_combo.currentText()), scanner_id)
        if roi:
            self._roi_lx = roi.x
            self._roi_rx = roi.x + roi.w
        else:
            self._roi_lx = 0
            self._roi_rx = W
        self._roi_status_lbl.setText(f"Frame: {path.name}  ({W}x{H})")
        self._btn_roi_save.setEnabled(True)
        self._roi_redraw()

    _ROI_DEFAULT_DIR = Path(r"C:\DEFYVISION - Metalconf\app_defyvision_metalconf\data\recordings")

    def _roi_start_dir(self) -> str:
        if self._rec_dir and self._rec_dir.exists():
            return str(self._rec_dir)
        if self._ROI_DEFAULT_DIR.exists():
            return str(self._ROI_DEFAULT_DIR)
        return ""

    def _on_roi_pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar imagen",
            self._roi_start_dir(),
            "Imágenes (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            self._roi_load_frame(Path(path))

    def _on_roi_pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de frames",
            self._roi_start_dir()
        )
        if not folder:
            return
        exts = {".png", ".jpg", ".jpeg", ".bmp"}
        frames = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts)
        if not frames:
            self._roi_status_lbl.setText("La carpeta no tiene imágenes")
            return
        # Mostrar el primer frame
        self._roi_load_frame(frames[0])

    def _roi_move(self, edge: str, direction: int) -> None:
        if self._roi_frame is None:
            return
        step = self._spin_roi_step.value() * direction
        W = self._roi_frame.shape[1]
        if edge == "lx":
            self._roi_lx = max(0, min(self._roi_rx - 1, self._roi_lx + step))
        else:
            self._roi_rx = max(self._roi_lx + 1, min(W, self._roi_rx + step))
        self._roi_redraw()

    def _roi_redraw(self) -> None:
        if self._roi_frame is None:
            return
        import cv2
        lx, rx = self._roi_lx, self._roi_rx
        H, W   = self._roi_frame.shape[:2]

        # Etiquetas de posición
        self._roi_lx_lbl.setText(f"x={lx}")
        self._roi_rx_lbl.setText(f"x={rx}")
        self._roi_result_lbl.setText(f"w={rx - lx}")

        # Preview con líneas de borde
        vis = self._roi_frame.copy()
        # Área fuera del ROI oscurecida
        mask = vis.copy()
        mask[:, :lx]  = (mask[:, :lx]  * 0.3).astype("uint8")
        mask[:, rx:]  = (mask[:, rx:]  * 0.3).astype("uint8")
        vis = mask
        cv2.line(vis, (lx, 0), (lx, H - 1), (0, 255, 100), 2)
        cv2.line(vis, (rx, 0), (rx, H - 1), (0, 200, 255), 2)
        cv2.rectangle(vis, (lx, 2), (rx, H - 3), (255, 255, 255), 1)

        thumb_w = max(self._roi_preview_lbl.width(), 280)
        scale   = thumb_w / W
        thumb   = cv2.resize(vis, (thumb_w, int(H * scale)), interpolation=cv2.INTER_AREA)
        th, tw  = thumb.shape[:2]
        rgb     = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
        qimg    = QImage(rgb.data, tw, th, rgb.strides[0], QImage.Format.Format_RGB888)
        pix     = QPixmap.fromImage(qimg)
        self._roi_preview_lbl.setPixmap(
            pix.scaled(
                self._roi_preview_lbl.width(),
                self._roi_preview_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_roi_save(self) -> None:
        if self._roi_frame is None:
            return
        lx, rx = self._roi_lx, self._roi_rx
        if rx <= lx:
            return
        H = self._roi_frame.shape[0]
        scanner_id = self._roi_scanner_combo.currentText() or None
        model_disp = self._model_combo.currentText()
        from src.utils.model_names import to_internal as _to_int
        from src.patterns.roi import roi_path
        import json
        internal = _to_int(model_disp)
        path = roi_path(internal, scanner_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": lx, "y": 0, "w": rx - lx, "h": H}, indent=2), encoding="utf-8")
        self._roi_status_lbl.setText("ROI guardado — reconstruyendo patrón…")
        self._refresh_current_roi_label()

        # Reconstruir patrón automáticamente con el frame actual de calibración
        frame_copy = self._roi_frame.copy()
        self._rebuild_pattern_async(frame_copy, internal, scanner_id)

    def _rebuild_pattern_async(self, frame, model: str, scanner_id) -> None:
        """Reconstruye holes.json en background usando el frame de calibración."""
        import tempfile, os, cv2

        class _RebuildWorker(QThread):
            done   = pyqtSignal(str, bool)   # (mensaje, ok)

            def __init__(self, frame, model, scanner_id):
                super().__init__()
                self._frame      = frame
                self._model      = model
                self._scanner_id = scanner_id

            def run(self):
                tmp = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                        tmp = f.name
                    cv2.imwrite(tmp, self._frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    from src.patterns.pattern_build import build_pattern_from_image
                    out = build_pattern_from_image(
                        self._model,
                        Path(tmp),
                        scanner_id=self._scanner_id,
                    )
                    self.done.emit(f"ROI y patrón guardados → {out.name}", True)
                except Exception as exc:
                    self.done.emit(f"ROI guardado · Error reconstruyendo patrón: {exc}", False)
                finally:
                    if tmp and os.path.exists(tmp):
                        os.unlink(tmp)

        worker = _RebuildWorker(frame, model, scanner_id)
        worker.done.connect(self._on_rebuild_done)
        worker.done.connect(lambda *_: worker.deleteLater())
        self._rebuild_worker = worker   # retener referencia
        worker.start()

    def _on_rebuild_done(self, msg: str, ok: bool) -> None:
        color = "#22c55e" if ok else "#f87171"
        self._roi_status_lbl.setText(msg)
        self._roi_status_lbl.setStyleSheet(f"color:{color};font-size:11px;")

    # ------------------------------------------------------------------

    def _build_ip_camera_section(self) -> QGroupBox:
        grp = QGroupBox("CÁMARA IP EN VIVO")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(8)

        # ── URL row ──────────────────────────────────────────────────
        url_row = QHBoxLayout()
        url_row.setSpacing(8)

        url_lbl = QLabel("URL")
        url_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 8px;"
        )
        url_row.addWidget(url_lbl)

        self._ip_url_edit = QLineEdit()

        self._ip_url_edit.setText("")
        self._ip_url_edit.setPlaceholderText("http://ip/oneshotimage.jpg  o  rtsp://ip:554/live  o  http://ip/video.mjpg")
        self._ip_url_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:6px;padding:6px 10px;font-size:12px;font-family:Consolas,monospace;"
            f"selection-background-color:{_ACCENT};"
        )
        self._ip_url_edit.returnPressed.connect(self._on_ip_connect)
        url_row.addWidget(self._ip_url_edit, stretch=1)

        self._btn_ip_connect = self._mk_btn("Conectar", "#15803d", h=36, fs=12)
        self._btn_ip_disconnect = self._mk_btn("Desconectar", "#991b1b", h=36, fs=12)
        self._btn_ip_disconnect.setEnabled(False)
        self._btn_ip_connect.clicked.connect(self._on_ip_connect)
        self._btn_ip_disconnect.clicked.connect(self._on_ip_disconnect)
        url_row.addWidget(self._btn_ip_connect)
        url_row.addWidget(self._btn_ip_disconnect)

        self._ip_status_lbl = QLabel("-")
        self._ip_status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
        )
        url_row.addWidget(self._ip_status_lbl)

        lay.addLayout(url_row)

        # ── Preview ──────────────────────────────────────────────────
        self._ip_preview = QLabel("Sin señal")
        self._ip_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ip_preview.setMinimumHeight(280)
        self._ip_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ip_preview.setStyleSheet(
            f"background:#0a0f1a;border-radius:8px;border:1px solid {_BORDER};"
            f"color:{_MUTED};font-size:12px;"
        )
        lay.addWidget(self._ip_preview, stretch=1)

        return grp

    def _on_ip_connect(self) -> None:
        url = self._ip_url_edit.text().strip()
        if not url:
            self._ip_status_lbl.setText("Ingrese una URL")
            return
        self._on_ip_disconnect()
        self._ip_status_lbl.setText("Conectando...")
        # Accept integer index (e.g. "0") or full URL string
        source = int(url) if url.isdigit() else url
        if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
            auth = self._ip_auth_settings()
            self._ip_worker = _MJPEGReader(
                source,
                self,
                username=auth.get("username"),
                password=auth.get("password"),
            )
            self._ip_worker.frame_ready.connect(self._on_ip_frame_ready)
            self._ip_worker.error_occurred.connect(self._on_ip_error)
            self._ip_worker.start()
            self._btn_ip_connect.setEnabled(False)
            self._btn_ip_disconnect.setEnabled(True)
            self._ip_url_edit.setEnabled(False)
            self._ip_preview.setText("")
            return
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            self._ip_status_lbl.setText("No se pudo conectar")
            cap.release()
            return
        self._ip_cap = cap
        self._ip_timer.start(200)   # ~5 fps para la vista previa
        self._btn_ip_connect.setEnabled(False)
        self._btn_ip_disconnect.setEnabled(True)
        self._ip_url_edit.setEnabled(False)
        self._ip_preview.setText("")
        self._ip_status_lbl.setText("Conectado")

    def _on_ip_disconnect(self) -> None:
        self._ip_timer.stop()
        if self._ip_worker is not None:
            self._ip_worker.stop()
            self._ip_worker = None
        if self._ip_cap is not None:
            self._ip_cap.release()
            self._ip_cap = None
        self._ip_preview.setPixmap(QPixmap())
        self._ip_preview.setText("Sin señal")
        self._btn_ip_connect.setEnabled(True)
        self._btn_ip_disconnect.setEnabled(False)
        self._ip_url_edit.setEnabled(True)
        self._ip_status_lbl.setText("-")

    def _on_ip_error(self, msg: str) -> None:
        self._on_ip_disconnect()
        self._ip_status_lbl.setText(f"Error: {msg}")

    def _on_ip_frame_ready(self, frame) -> None:
        self._ip_status_lbl.setText("Conectado")
        self._show_ip_frame(frame)

    def _ip_auth_settings(self) -> dict:
        scanner_id = self._scanner_combo.currentText() if hasattr(self, "_scanner_combo") else ""
        if scanner_id:
            settings = camera_config.load_camera_settings(scanner_id)
            if settings.get("username") and settings.get("password"):
                return settings
        for sid in self._system.scanner_ids():
            settings = camera_config.load_camera_settings(sid)
            if settings.get("username") and settings.get("password"):
                return settings
        return {}

    def _refresh_ip_camera(self) -> None:
        if self._ip_cap is None or not self._ip_cap.isOpened():
            self._on_ip_disconnect()
            return
        ret, frame = self._ip_cap.read()
        if not ret:
            return
        self._show_ip_frame(frame)

    def _show_ip_frame(self, frame) -> None:
        rect = self._ip_preview.contentsRect()
        w = max(640, rect.width() - 4)
        h = max(400, rect.height() - 4)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        fh, fw = rgb.shape[:2]
        qi  = QImage(rgb.data, fw, fh, fw * 3, QImage.Format.Format_RGB888)
        pxm = QPixmap.fromImage(qi).scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._ip_preview.setPixmap(pxm)

    def _build_analysis_section(self) -> QGroupBox:
        grp = QGroupBox("ANÁLISIS")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(16, 20, 16, 16)
        lay.setSpacing(12)

        # ── Fila 0: selector de modelo (replica de GRABACIÓN) ──────────
        model_row = QHBoxLayout()
        model_row.setSpacing(8)

        model_chip = QLabel("TIPO DE PLACA")
        model_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 8px;margin-right:4px;"
        )
        model_row.addWidget(model_chip)

        self._btn_model_esterilla_ana = QPushButton("ESTERILLA")
        self._btn_model_esterilla_ana.setCheckable(True)
        self._btn_model_esterilla_ana.setFixedHeight(34)
        self._btn_model_microperf_ana = QPushButton("MICROPERFORADO")
        self._btn_model_microperf_ana.setCheckable(True)
        self._btn_model_microperf_ana.setFixedHeight(34)

        self._model_btn_group_ana = QButtonGroup(self)
        self._model_btn_group_ana.setExclusive(True)
        self._model_btn_group_ana.addButton(self._btn_model_esterilla_ana, 0)
        self._model_btn_group_ana.addButton(self._btn_model_microperf_ana, 1)

        self._btn_model_esterilla_ana.toggled.connect(
            lambda checked: self._on_model_btn_toggled("Esterilla", checked)
        )
        self._btn_model_microperf_ana.toggled.connect(
            lambda checked: self._on_model_btn_toggled("Microperforado", checked)
        )

        model_row.addWidget(self._btn_model_esterilla_ana)
        model_row.addSpacing(4)
        model_row.addWidget(self._btn_model_microperf_ana)
        model_row.addStretch()
        lay.addLayout(model_row)

        # ── Fila 0b: selector de scanner ──────────────────────────────
        scanner_row = QHBoxLayout()
        scanner_row.setSpacing(8)

        scanner_chip = QLabel("SCANNER")
        scanner_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 8px;margin-right:4px;"
        )
        scanner_row.addWidget(scanner_chip)

        self._ana_scanner_combo = self._make_combo(self._system.scanner_ids(), min_w=120)
        if hasattr(self, "_scanner_combo"):
            self._ana_scanner_combo.setCurrentText(self._scanner_combo.currentText())
        scanner_row.addWidget(self._ana_scanner_combo)
        scanner_row.addStretch()
        lay.addLayout(scanner_row)

        # ── Fila 1: botones de acción ─────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_load    = self._mk_btn("📂  Abrir grabación", "#374151", h=44, fs=13)
        self._btn_analyze = self._mk_btn("▶  Analizar",         "#1d4ed8", h=44, fs=14)
        self._btn_analyze.setEnabled(False)
        self._btn_stop_analyze = self._mk_btn("⏹  Detener",     "#b91c1c", h=44, fs=13)
        self._btn_stop_analyze.setEnabled(False)
        self._btn_stop_analyze.clicked.connect(self._on_stop_analyze)

        btn_row.addWidget(self._btn_load)
        btn_row.addWidget(self._btn_analyze)
        btn_row.addWidget(self._btn_stop_analyze)
        btn_row.addStretch()

        # Chip de tipo de placa
        self._analyze_model_chip = QLabel("-")
        self._analyze_model_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._analyze_model_chip.setFixedHeight(44)
        self._analyze_model_chip.setMinimumWidth(140)
        btn_row.addWidget(self._analyze_model_chip)

        lay.addLayout(btn_row)

        # ── Fila 2: barra de progreso ────────────────────────────────
        self._ana_progress_bar = QProgressBar()
        self._ana_progress_bar.setRange(0, 100)
        self._ana_progress_bar.setValue(0)
        self._ana_progress_bar.setFixedHeight(18)
        self._ana_progress_bar.setVisible(False)
        self._ana_progress_bar.setStyleSheet(
            f"QProgressBar {{ background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:8px;text-align:center;font-size:10px;font-weight:700;"
            f"color:{_TEXT}; }}"
            f"QProgressBar::chunk {{ background:{_ACCENT};border-radius:8px; }}"
        )
        lay.addWidget(self._ana_progress_bar)

        # ── Fila 3: texto de progreso + estado ────────────────────────
        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        self._ana_progress = QLabel("Listo para analizar")
        self._ana_progress.setStyleSheet(
            f"color:{_ACCENT};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        info_row.addWidget(self._ana_progress)
        info_row.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-family:Consolas;"
        )
        info_row.addWidget(self._status_lbl)
        lay.addLayout(info_row)

        # ── Fila 3: resumen de resultados ─────────────────────────────
        self._summary_row = QHBoxLayout()
        self._summary_row.setSpacing(8)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:700;letter-spacing:0.5px;"
        )
        self._summary_row.addWidget(self._summary_lbl)
        self._summary_row.addStretch()

        self._stats_lbl = QLabel("")
        self._stats_lbl.setWordWrap(True)
        self._stats_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-family:Consolas;"
        )
        self._summary_row.addWidget(self._stats_lbl)
        lay.addLayout(self._summary_row)
        return grp

    def _build_browser_section(self) -> QGroupBox:
        grp = QGroupBox("NAVEGADOR DE CAPTURAS")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(10)

        # ── Navigation bar ────────────────────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(4)

        _NAV_BASE = (
            "QPushButton {{"
            "  background:{bg};color:{fg};border-radius:7px;"
            "  font-size:{fs}px;font-weight:700;border:1px solid {bd};padding:0 {pad}px;"
            "}}"
            "QPushButton:hover {{"
            "  background:{hv};border-color:#64748b;"
            "}}"
            "QPushButton:pressed {{ background:#0f172a; }}"
            "QPushButton:disabled {{ background:#131e2e;color:#374151;border-color:#1e293b; }}"
        )

        def _nav_btn(label, bg="#1e293b", fg=_TEXT, bd=_BORDER, hv="#2d3f55",
                     fs=13, w=None, h=38, tooltip="", pad=10):
            b = QPushButton(label)
            b.setToolTip(tooltip)
            b.setFixedHeight(h)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if w:
                b.setFixedWidth(w)
            b.setStyleSheet(_NAV_BASE.format(
                bg=bg, fg=fg, bd=bd, hv=hv, fs=fs, pad=pad
            ))
            return b

        # First / Last - outlined style
        self._btn_first = _nav_btn("|<", w=40, tooltip="Primer frame", pad=0)
        self._btn_last  = _nav_btn(">|", w=40, tooltip="Último frame",  pad=0)

        # ±10 - larger with text
        self._btn_prev10 = _nav_btn("<<  -10", w=74, tooltip="Retroceder 10 frames",
                                     bg="#1e293b", bd="#334155", hv="#2d3f55", fs=12)
        self._btn_next10 = _nav_btn("+10  >>", w=74, tooltip="Avanzar 10 frames",
                                     bg="#1e293b", bd="#334155", hv="#2d3f55", fs=12)

        # ±1 - filled accent style
        self._btn_prev = _nav_btn("<  Ant.", w=76, tooltip="Frame anterior",
                                   bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8", fs=12)
        self._btn_next = _nav_btn("Sig.  >", w=76, tooltip="Frame siguiente",
                                   bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8", fs=12)

        # Frame counter
        self._nav_lbl = QLabel("-")
        self._nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_lbl.setMinimumWidth(96)
        self._nav_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:15px;font-weight:700;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:7px;padding:4px 16px;letter-spacing:2px;"
        )

        for w in (self._btn_first, self._btn_prev10, self._btn_prev):
            nav.addWidget(w)
        nav.addSpacing(6)
        nav.addWidget(self._nav_lbl)
        nav.addSpacing(6)
        for w in (self._btn_next, self._btn_next10, self._btn_last):
            nav.addWidget(w)

        nav.addSpacing(14)
        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"color:{_BORDER};")
        nav.addWidget(sep)
        nav.addSpacing(10)

        # ── NOK navigator ─────────────────────────────────────────────
        self._btn_prev_nok = _nav_btn("< NOK", w=72, tooltip="Frame NOK anterior",
                                      bg="#3b0f0f", bd="#7f1d1d", hv="#5c1515", fs=11)
        self._btn_next_nok = _nav_btn("NOK >", w=72, tooltip="Frame NOK siguiente",
                                      bg="#3b0f0f", bd="#7f1d1d", hv="#5c1515", fs=11)
        self._nok_nav_lbl = QLabel("NOK -")
        self._nok_nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nok_nav_lbl.setFixedWidth(68)
        self._nok_nav_lbl.setStyleSheet(
            f"color:{_NOK};font-size:11px;font-weight:700;"
            f"background:#1a0a0a;border:1px solid #7f1d1d;"
            "border-radius:7px;padding:4px 8px;"
        )
        self._btn_prev_nok.setEnabled(False)
        self._btn_next_nok.setEnabled(False)
        for w in (self._btn_prev_nok, self._nok_nav_lbl, self._btn_next_nok):
            nav.addWidget(w)
        nav.addSpacing(10)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"color:{_BORDER};")
        nav.addWidget(sep2)
        nav.addSpacing(10)

        # Overlay toggle - pill style
        self._overlay_toggle = QPushButton("OVERLAY")
        self._overlay_toggle.setCheckable(True)
        self._overlay_toggle.setChecked(True)
        self._overlay_toggle.setFixedHeight(38)
        self._overlay_toggle.setStyleSheet(
            f"QPushButton:checked {{"
            f"  background:#0c4a6e;color:{_ACCENT};"
            f"  border-radius:7px;font-size:11px;font-weight:700;padding:0 16px;"
            f"  border:1px solid {_ACCENT};"
            "}}"
            f"QPushButton:!checked {{"
            f"  background:{_PANEL};color:{_MUTED};"
            "  border-radius:7px;font-size:11px;font-weight:700;padding:0 16px;"
            f"  border:1px solid {_BORDER};"
            "}}"
            "QPushButton:hover { border-color:#64748b; }"
        )
        nav.addWidget(self._overlay_toggle)
        nav.addSpacing(4)

        self._btn_fit = _nav_btn("Ajustar", bg="#0f3460", bd="#1d4ed8", hv="#1e40af",
                                  tooltip="Ajustar imagen a ventana (doble clic en imagen)")
        nav.addWidget(self._btn_fit)
        nav.addStretch()

        # ── Model chip - shows which pattern type was used for analysis ─
        self._model_chip = QLabel("-")
        self._model_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._model_chip.setFixedHeight(38)
        self._model_chip.setMinimumWidth(110)
        self._model_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:2px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:7px;padding:0 14px;"
        )
        nav.addSpacing(8)
        nav.addWidget(self._model_chip)
        nav.addSpacing(8)

        # ── Result card - right of nav bar ────────────────────────────
        self._result_card = QFrame()
        self._result_card.setMinimumWidth(210)
        self._result_card.setStyleSheet(
            f"QFrame {{ background:{_PANEL};border:1px solid {_BORDER};border-radius:8px; }}"
        )
        rc_lay = QHBoxLayout(self._result_card)
        rc_lay.setContentsMargins(14, 0, 14, 0)
        self._result_lbl = QLabel("-")
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_lbl.setStyleSheet(f"font-size:13px;font-weight:700;color:{_MUTED};")
        rc_lay.addWidget(self._result_lbl)
        nav.addWidget(self._result_card)

        lay.addLayout(nav)

        # ── Separator ─────────────────────────────────────────────────
        lay.addWidget(self._hline())

        # ── Save / Export row ─────────────────────────────────────────
        save = QHBoxLayout()
        save.setSpacing(10)

        self._btn_save_current = self._mk_btn("Guardar frame", "#065f46", h=36, fs=11)
        self._btn_save_current.setEnabled(False)
        self._btn_save_current.setToolTip("Guarda el overlay del frame actual en data/output/export/")
        save.addWidget(self._btn_save_current)

        save.addWidget(self._vline())

        # Export group - visual container
        exp_card = QFrame()
        exp_card.setStyleSheet(
            f"QFrame {{ background:{_DARK};border:1px solid {_BORDER};border-radius:7px; }}"
        )
        eg = QHBoxLayout(exp_card)
        eg.setContentsMargins(12, 6, 12, 6)
        eg.setSpacing(8)

        exp_title = QLabel("Exportar rango")
        exp_title.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
        )
        eg.addWidget(exp_title)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"color:{_BORDER};")
        eg.addWidget(sep2)

        lbl_from = QLabel("desde")
        lbl_from.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        eg.addWidget(lbl_from)

        self._spin_from = QSpinBox()
        self._spin_from.setRange(1, 1)
        self._spin_from.setValue(1)
        self._spin_from.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:3px 6px;font-size:12px;max-width:72px;"
        )
        eg.addWidget(self._spin_from)

        lbl_to = QLabel("hasta")
        lbl_to.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        eg.addWidget(lbl_to)

        self._spin_to = QSpinBox()
        self._spin_to.setRange(1, 1)
        self._spin_to.setValue(1)
        self._spin_to.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:3px 6px;font-size:12px;max-width:72px;"
        )
        eg.addWidget(self._spin_to)

        save.addWidget(exp_card)

        self._btn_export = self._mk_btn("Exportar 0 frames", "#0f3460", h=36, fs=11)
        self._btn_export.setEnabled(False)
        self._btn_export.setToolTip("Exporta los overlays del rango seleccionado a data/output/export/")
        save.addWidget(self._btn_export)

        save.addStretch()

        self._export_status_lbl = QLabel("")
        self._export_status_lbl.setStyleSheet(
            f"color:{_OK};font-size:10px;font-family:Consolas;"
        )
        save.addWidget(self._export_status_lbl)

        lay.addLayout(save)

        # ── Separator ─────────────────────────────────────────────────
        lay.addWidget(self._hline())

        # ── Image viewer ──────────────────────────────────────────────
        self._img_view = ZoomableImageView("Sin frames")
        self._img_view.setMinimumHeight(560)
        self._img_view.installEventFilter(self)
        lay.addWidget(self._img_view, stretch=1)

        return grp

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        sid = self._scanner_combo.currentText()
        cam = self._system.camera(sid)
        if not cam.is_running:
            self._status_lbl.setText("ALERTA  La cámara no está activa")
            return

        from datetime import datetime as _dt
        rec_date = _dt.now().strftime("%d-%m-%Y")
        rec_name = self._build_recording_folder_name(rec_date)
        self._rec_dir = self._unique_recording_dir(rec_name)
        self._rec_dir.mkdir(parents=True, exist_ok=True)
        self._frame_paths.clear()
        self._results.clear()
        self._current_idx = 0
        self._summary_lbl.setText("")
        self._stats_lbl.setText("")
        self._ana_progress.setText("")
        self._export_status_lbl.setText("")
        self._img_view.clear("Sin frames")
        self._update_nav_state()
        self._update_export_range_max()

        _CAM_PARAMS = [
            "focus", "exposure", "white_balance", "gain", "brightness",
            "contrast", "saturation", "sharpness", "gamma", "backlight_compensation",
        ]
        cam_settings: dict = {}
        try:
            for param in _CAM_PARAMS:
                v = cam.read_setting(param)
                if v >= 0:
                    cam_settings[param] = int(v)
        except Exception:
            pass

        meta = {
            "model": self._active_model(),
            "model_display": self._model_combo.currentText(),
            "recording_folder": self._rec_dir.name,
            "fps": self._fps_spin.value(),
            "scanner": self._scanner_combo.currentText(),
            "timestamp": _dt.now().isoformat(),
            "camera_settings": cam_settings,
            "cam_fps_real": cam.fps if cam.fps > 0 else None,
        }
        (self._rec_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        from concurrent.futures import ThreadPoolExecutor
        self._write_executor = ThreadPoolExecutor(max_workers=1)
        self._px_cache.clear()
        self._last_card_state = None

        interval_ms = max(16, 1000 // self._fps_spin.value())
        self._rec_timer.start(interval_ms)
        self._recording = True

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_analyze.setEnabled(False)
        self._btn_load.setEnabled(False)
        self._scanner_combo.setEnabled(False)
        self._btn_model_esterilla.setEnabled(False)
        self._btn_model_microperf.setEnabled(False)
        self._fps_spin.setEnabled(False)
        self._live_chk.setEnabled(False)

        mode_txt = " (en vivo)" if self._live_chk.isChecked() else ""
        self._status_lbl.setText(f"Grabando{mode_txt} -> {self._rec_dir.name}")
        self._set_rec_badge("recording", 0, self._rec_dir)
        logger.info(f"[Grabación] inicio en {self._rec_dir}  modelo={meta['model_display']}  fps={meta['fps']}")

    def _on_stop(self) -> None:
        self._rec_timer.stop()
        self._recording = False
        self._live_ms_detector = None
        self._live_pre = None
        self._live_session = None
        if self._write_executor is not None:
            self._write_executor.shutdown(wait=True)   # flush pending PNG writes
            self._write_executor = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_load.setEnabled(True)
        self._scanner_combo.setEnabled(True)
        self._btn_model_esterilla.setEnabled(True)
        self._btn_model_microperf.setEnabled(True)
        self._fps_spin.setEnabled(True)
        self._live_chk.setEnabled(True)

        n = len(self._frame_paths)
        self._status_lbl.setText(f"Detenido - {n} frames en {self._rec_dir.name}")
        self._set_rec_badge("ready", n, self._rec_dir)
        self._btn_analyze.setEnabled(n > 0)
        self._update_export_range_max()
        if n > 0 and not self._results:
            self._show_frame(0)
        logger.info(f"[Grabación] detenida - {n} frames")

    def _grab_frame(self) -> None:
        sid  = self._scanner_combo.currentText()
        cam  = self._system.camera(sid)
        frame = cam.get_frame()
        if frame is None:
            return
        idx  = len(self._frame_paths)
        path = self._rec_dir / f"frame_{idx:04d}.png"
        self._frame_paths.append(path)
        self._set_rec_badge("recording", idx + 1, self._rec_dir)

        # Write PNG in background - PNG compression can take 50-200ms and must not
        # block the main thread. compression=1 (fastest) trades size for speed.
        frame_copy = frame.copy()
        if self._write_executor is not None:
            self._write_executor.submit(
                cv2.imwrite, str(path), frame_copy,
                [cv2.IMWRITE_PNG_COMPRESSION, 1],
            )
        else:
            cv2.imwrite(str(path), frame_copy, [cv2.IMWRITE_PNG_COMPRESSION, 1])

        if self._live_chk.isChecked():
            try:
                from src.vision.inspector import InspectionSession
                from src.utils.config import load_tolerances

                model      = self._active_model()
                scanner_id = self._scanner_combo.currentText() or None

                if self._live_session is None:
                    tols = load_tolerances(model, scanner_id=scanner_id)
                    self._live_session = InspectionSession(
                        model,
                        scanner_id=scanner_id,
                        movement_threshold=float(tols.get("continuous_position_threshold", 0.0)),
                        min_interval_sec=0.0,
                    )

                result = self._live_session.inspect_frame(
                    frame_copy,
                    frame_id=path.stem,
                    force=False,
                )
                if result is None:
                    return
                self._results.append(result)
                ok  = sum(1 for r in self._results if r.status == "OK")
                nok = len(self._results) - ok
                self._summary_lbl.setText(f"OK: {ok}  NOK: {nok}  Total: {len(self._results)}")
                self._show_frame(idx)
            except Exception as exc:
                logger.error(f"[Live análisis] error en frame {idx}: {exc}")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _set_analysis_running(self, running: bool) -> None:
        """Bloquea/activa los controles que NO deben cambiar durante el análisis."""
        self._btn_analyze.setEnabled(not running and bool(self._frame_paths))
        self._btn_stop_analyze.setEnabled(running)
        self._btn_load.setEnabled(not running)
        self._btn_model_esterilla.setEnabled(not running)
        self._btn_model_microperf.setEnabled(not running)
        if hasattr(self, "_btn_model_esterilla_ana"):
            self._btn_model_esterilla_ana.setEnabled(not running)
            self._btn_model_microperf_ana.setEnabled(not running)
        if hasattr(self, "_scanner_combo"):
            self._scanner_combo.setEnabled(not running)
        if hasattr(self, "_ana_scanner_combo"):
            self._ana_scanner_combo.setEnabled(not running)

    def _on_analyze(self) -> None:
        if not self._frame_paths:
            return
        if self._write_executor is not None:
            self._write_executor.shutdown(wait=True)
            self._write_executor = None
        self._px_cache.clear()
        self._last_card_state = None
        self._results.clear()
        self._overlay_jpegs.clear()
        self._stats_lbl.setText("")
        self._export_status_lbl.setText("")

        model      = self._active_model()
        scanner_id = self._ana_scanner_combo.currentText() or None
        n          = len(self._frame_paths)
        if self._worker is not None and self._worker.isRunning():
            return
        self._ana_model      = model
        self._ana_scanner_id = scanner_id
        self._ana_frame_idx  = 0
        self._ana_running    = True
        _bar_style = (
            f"QProgressBar {{ background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:8px;text-align:center;font-size:10px;font-weight:700;"
            f"color:{_TEXT}; }}"
            f"QProgressBar::chunk {{ background:{_ACCENT};border-radius:8px; }}"
        )
        self._ana_progress.setText(f"Analizando  0 / {n}  (0%)")
        self._ana_progress.setStyleSheet(
            f"color:{_ACCENT};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        self._ana_progress_bar.setRange(0, n if n > 0 else 1)
        self._ana_progress_bar.setValue(0)
        self._ana_progress_bar.setStyleSheet(_bar_style)
        self._ana_progress_bar.setVisible(True)
        self._set_rec_badge("analyzing", n, self._rec_dir)
        self._set_analysis_running(True)
        logger.info(f"[Analisis] iniciando: {n} frames  modelo={model}")
        self._worker = _AnalysisWorker(model, list(self._frame_paths), scanner_id, self)
        self._worker.progress.connect(self._on_ana_progress)
        self._worker.finished.connect(self._on_ana_done)
        self._worker.error.connect(self._on_ana_error)
        self._worker.cancelled.connect(self._on_ana_cancelled)
        self._worker.start()
        return

        # Pre-cargar patrón y tolerancias (una sola lectura de disco para todos los frames)
        try:
            from src.patterns.pattern_io import load_pattern, find_pattern_path
            from src.patterns.roi import load_roi
            from src.utils.config import load_tolerances
            from src.pipeline.machine_stop import MachineStopDetector
            tols = load_tolerances(model, scanner_id=scanner_id)
            self._ana_pre = {
                "tolerances": tols,
                "pattern":    load_pattern(find_pattern_path(model, scanner_id)),
                "roi":        load_roi(model, scanner_id),
                "ema_state":  {},
            }
            if bool(tols.get("machine_stop_enabled", False)):
                self._ana_pre["machine_stop_detector"] = MachineStopDetector(
                    enabled=True,
                    missing_frames=int(tols.get("machine_stop_missing_frames", 5)),
                    min_missing=int(tols.get("machine_stop_min_missing", 1)),
                    same_zone_px=float(tols.get("machine_stop_same_zone_px", 35.0)),
                    ignore_near_miss=bool(tols.get("machine_stop_ignore_near_miss", True)),
                    track_by_grid=bool(tols.get("machine_stop_track_by_grid", True)),
                    same_column_tol_cells=int(tols.get("machine_stop_same_column_tol_cells", 0)),
                )
        except Exception as exc:
            self._ana_progress.setText(f"✗  Error al cargar patrón: {exc}")
            self._ana_progress.setStyleSheet(
                f"color:{_NOK};font-size:13px;font-family:Consolas;font-weight:700;"
            )
            logger.error(f"[Análisis] error cargando patrón: {exc}", exc_info=True)
            return

        self._ana_model      = model
        self._ana_scanner_id = scanner_id
        self._ana_frame_idx  = 0
        self._ana_running    = True

        _bar_style = (
            f"QProgressBar {{ background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:8px;text-align:center;font-size:10px;font-weight:700;"
            f"color:{_TEXT}; }}"
            f"QProgressBar::chunk {{ background:{_ACCENT};border-radius:8px; }}"
        )
        self._ana_progress.setText(f"Analizando  0 / {n}  (0%)")
        self._ana_progress.setStyleSheet(
            f"color:{_ACCENT};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        self._ana_progress_bar.setRange(0, n if n > 0 else 1)
        self._ana_progress_bar.setValue(0)
        self._ana_progress_bar.setStyleSheet(_bar_style)
        self._ana_progress_bar.setVisible(True)
        self._set_rec_badge("analyzing", n, self._rec_dir)
        self._set_analysis_running(True)
        logger.info(f"[Análisis] iniciando: {n} frames  modelo={model}")

        # Pausar timer de cámara durante el análisis para evitar que processEvents()
        # quede bloqueado esperando respuesta HTTP de la cámara.
        self._ip_timer.stop()

        # Procesar frames uno a uno via QTimer — corre en hilo principal, sin
        # dependencia de entrega de signals cross-thread.
        QTimer.singleShot(5, self._analyze_one_frame)

    def _analyze_one_frame(self) -> None:
        """Procesa un frame y programa el siguiente. Corre en el hilo principal."""
        try:
            self._analyze_one_frame_inner()
        except Exception as exc:
            import traceback
            logger.error(f"[Análisis] excepción no capturada: {exc}\n{traceback.format_exc()}")
            self._ana_running = False
            self._ip_timer.start(200)
            self._on_ana_error(f"Error inesperado: {exc}")

    def _analyze_one_frame_inner(self) -> None:
        if not self._ana_running:
            return

        n = len(self._frame_paths)
        i = self._ana_frame_idx

        if i >= n:
            self._ana_running = False
            self._ip_timer.start(200)
            try:
                self._on_ana_done_inner(self._results)
            except Exception as exc:
                logger.error(f"[Análisis] error procesando resultados: {exc}", exc_info=True)
                self._ana_progress.setText(f"✗  Error al procesar: {exc}")
                self._ana_progress.setStyleSheet(
                    f"color:{_NOK};font-size:13px;font-family:Consolas;font-weight:700;"
                )
                self._ana_progress_bar.setVisible(False)
                self._set_analysis_running(False)
            return

        # Mostrar "procesando frame N" ANTES de la llamada (que puede tardar 300ms+).
        pct_before = int(i * 100 / n)
        self._ana_progress.setText(
            f"Analizando frame  {i + 1} / {n}  ({pct_before}%)..."
        )
        self._ana_progress_bar.setValue(i)
        QApplication.processEvents()

        from src.inspection import inspect_image
        try:
            result = inspect_image(
                self._ana_model, self._frame_paths[i],
                scanner_id=self._ana_scanner_id,
                _preloaded=self._ana_pre,
            )
            self._results.append(result)
        except Exception as exc:
            self._ana_running = False
            self._ip_timer.start(200)
            logger.error(f"[Análisis] error en frame {i}: {exc}", exc_info=True)
            self._on_ana_error(str(exc))
            return

        self._ana_frame_idx += 1
        done = self._ana_frame_idx
        pct_after = int(done * 100 / n)
        self._ana_progress_bar.setValue(done)
        self._ana_progress.setText(f"Analizando  {done} / {n}  ({pct_after}%)")
        logger.info(f"[Análisis] frame {done}/{n} ({pct_after}%)")

        if done % 3 == 0:
            self._show_frame(i)

        QTimer.singleShot(10, self._analyze_one_frame)

    def _on_stop_analyze(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._btn_stop_analyze.setEnabled(False)
            self._ana_progress.setText("Deteniendo...")
            return
        if self._ana_running:
            self._ana_running = False
            self._ip_timer.start(200)
            done = self._ana_frame_idx
            self._ana_progress.setText(f"Detenido  ({done} frames procesados)")
            self._ana_progress.setStyleSheet(
                f"color:{_WARN};font-size:13px;font-family:Consolas;font-weight:700;"
            )
            self._ana_progress_bar.setVisible(False)
            self._set_analysis_running(False)
            self._set_rec_badge("ready", len(self._frame_paths), self._rec_dir)
            logger.info(f"[Grabación] análisis detenido tras {done} frames")
            return
    def _on_ana_cancelled(self, done: int) -> None:
        self._ana_running = False
        self._ana_frame_idx = done
        self._worker = None
        self._ana_progress.setText(f"Detenido  ({done} frames procesados)")
        self._ana_progress.setStyleSheet(
            f"color:{_WARN};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        self._ana_progress_bar.setVisible(False)
        self._set_analysis_running(False)
        self._set_rec_badge("ready", len(self._frame_paths), self._rec_dir)
        logger.info(f"[Grabación] análisis detenido por el operador tras {done} frames")

    def _on_ana_progress(self, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        self._ana_frame_idx = done
        self._ana_progress.setText(f"Analizando  {done} / {total}  ({pct}%)")
        self._ana_progress_bar.setValue(done)
        logger.debug(f"[Análisis] progreso {done}/{total} ({pct}%)")

    def _on_ana_done(self, results: list) -> None:
        self._ana_running = False
        self._worker = None
        try:
            self._on_ana_done_inner(results)
        except Exception as exc:
            logger.error(f"[Análisis] error al procesar resultados: {exc}", exc_info=True)
            self._ana_progress.setText(f"Error al procesar resultados: {exc}")
            self._set_analysis_running(False)

    def _on_ana_done_inner(self, results: list) -> None:
        self._results = results
        ok  = sum(1 for r in results if r.status == "OK")
        nok = len(results) - ok
        pct = round(100 * ok / len(results)) if results else 0

        from src.inspection import _apply_temporal_rule
        from src.utils.config import load_tolerances
        model  = self._active_model()
        tols   = load_tolerances(model, scanner_id=self._ana_scanner_id)
        consec = int(tols.get("consecutive_nok_frames", 5))
        temporal = _apply_temporal_rule(results, consec)
        t_ok  = sum(1 for t in temporal if t.decision_status == "OK")
        t_nok = len(temporal) - t_ok
        t_pct = round(100 * t_ok / len(temporal)) if temporal else 0

        ok_color  = _OK  if ok  > 0 else _MUTED
        nok_color = _NOK if nok > 0 else _MUTED
        self._summary_lbl.setText(
            f"Frame  OK: {ok} ({pct}%)   NOK: {nok}   Total: {len(results)}"
            f"    │    Temporal OK {t_ok} ({t_pct}%)  NOK {t_nok}   [umbral {consec}]"
        )

        if results:
            missing_counts = [len(r.report.missing_points) for r in results]
            avg_m = sum(missing_counts) / len(missing_counts)
            min_m, max_m = min(missing_counts), max(missing_counts)
            shifts  = [r.shift_xy for r in results if r.shift_xy is not None]
            offsets = [r.centering.offset_px for r in results if r.centering is not None]
            parts   = [f"missing avg={avg_m:.1f}  min={min_m}  max={max_m}"]
            if shifts:
                import math
                mags = [math.hypot(s[0], s[1]) for s in shifts]
                parts.append(f"shift avg={sum(mags)/len(mags):.1f}px  max={max(mags):.1f}px")
            if offsets:
                avg_off = sum(offsets) / len(offsets)
                max_off = max(abs(o) for o in offsets)
                parts.append(f"centro avg={avg_off:+.1f}px  max={max_off:.1f}px")
            self._stats_lbl.setText("    ".join(parts))

        # Comprimir overlays a JPEG y liberar los arrays BGR/mask de cada resultado.
        # 200 overlays de 1920x1080x3 (~6 MB) + masks (~2 MB) son ~1.6 GB en RAM, lo que
        # hacía swapear la PC al navegar. Comprimidos quedan ~60-120 MB; se decodifican
        # bajo demanda (rápido) y se cachean como pixmaps.
        self._overlay_jpegs = [None] * len(results)
        for _i, _r in enumerate(results):
            _ov = getattr(_r, "overlay", None)
            if _ov is not None:
                _ok, _buf = cv2.imencode(".jpg", _ov, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if _ok:
                    self._overlay_jpegs[_i] = _buf
            # Liberar arrays pesados (dataclass frozen → setattr directo).
            try:
                object.__setattr__(_r, "overlay", None)
                object.__setattr__(_r, "mask", None)
            except Exception:
                pass

        self._ana_progress.setText(f"✓  Análisis completo  —  OK: {ok}  NOK: {nok}  ({pct}%)")
        self._ana_progress.setStyleSheet(
            f"color:{_OK};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        self._ana_progress_bar.setValue(100)
        self._ana_progress_bar.setStyleSheet(
            f"QProgressBar {{ background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:8px;text-align:center;font-size:10px;font-weight:700;"
            f"color:{_TEXT}; }}"
            f"QProgressBar::chunk {{ background:{_OK};border-radius:8px; }}"
        )
        self._set_analysis_running(False)
        self._set_rec_badge("analyzed", len(results), self._rec_dir)
        self._update_model_chip()
        self._update_export_range_max()
        self._rebuild_nok_index()
        self._show_frame(0)
        logger.info(f"[Grabación] análisis completo - OK={ok} NOK={nok}")

    def _on_ana_error(self, msg: str) -> None:
        self._ana_running = False
        self._worker = None
        self._ana_progress.setText(f"✗  Error: {msg}")
        self._ana_progress.setStyleSheet(
            f"color:{_NOK};font-size:13px;font-family:Consolas;font-weight:700;"
        )
        self._ana_progress_bar.setVisible(False)
        self._set_analysis_running(False)
        self._set_rec_badge("ready", len(self._frame_paths), self._rec_dir)
        logger.error(f"[Grabación] error de análisis: {msg}")

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _result_bgr(self, idx: int):
        """Overlay BGR del resultado `idx`, decodificando del JPEG comprimido si existe.

        Devuelve None si no hay overlay disponible. Usado por el navegador y el export
        (los arrays BGR crudos se liberan tras el análisis para no saturar la RAM).
        """
        if 0 <= idx < len(self._overlay_jpegs) and self._overlay_jpegs[idx] is not None:
            return cv2.imdecode(self._overlay_jpegs[idx], cv2.IMREAD_COLOR)
        if 0 <= idx < len(self._results):
            return getattr(self._results[idx], "overlay", None)
        return None

    def _show_frame(self, idx: int) -> None:
        if not self._frame_paths:
            return
        first_load = self._current_idx == 0 and idx == 0 and self._img_view.current_pixmap() is None
        idx = max(0, min(idx, len(self._frame_paths) - 1))
        self._current_idx = idx

        show_ov = self._overlay_toggle.isChecked() and idx < len(self._results)
        cache_key = (idx, show_ov)

        pxm = self._px_cache.get(cache_key)
        if pxm is None:
            if show_ov:
                bgr = self._result_bgr(idx)
            else:
                bgr = cv2.imread(str(self._frame_paths[idx]))

            if bgr is not None:
                # cv2.cvtColor is faster than numpy channel-flip for large images.
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                qi  = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
                pxm = QPixmap.fromImage(qi)

                # Store in cache; evict entries farthest from current index when full.
                self._px_cache[cache_key] = pxm
                if len(self._px_cache) > self._px_cache_max:
                    keys_by_dist = sorted(
                        self._px_cache.keys(),
                        key=lambda k: abs(k[0] - idx),
                        reverse=True,
                    )
                    for k in keys_by_dist[self._px_cache_max // 2:]:
                        del self._px_cache[k]

        if pxm is not None:
            # auto_fit=True only when the image dimensions change (first load or new dataset).
            prev_pxm = self._img_view.current_pixmap()
            needs_fit = (prev_pxm is None
                         or prev_pxm.width() != pxm.width()
                         or prev_pxm.height() != pxm.height())
            self._img_view.set_pixmap(pxm, auto_fit=needs_fit)
            if first_load:
                self._img_view.setFocus()

        total = len(self._frame_paths)
        self._nav_lbl.setText(f"{idx + 1} / {total}")

        if idx < len(self._results):
            r = self._results[idx]
            missing = len(r.report.missing_points)
            center_txt = ""
            if r.centering is not None:
                sign = "+" if r.centering.offset_px >= 0 else ""
                center_txt = f"  -  centro {sign}{r.centering.offset_px:.1f}px"

            holes_nok = r.report.status == "NOK"
            if r.status == "OK":
                label, color, card_border = "OK", _OK, "#15803d"
            elif getattr(r, "centering_nok", False) and holes_nok:
                label, color, card_border = "NOK AGUJEROS+CENTRADO", _NOK, "#991b1b"
            elif getattr(r, "centering_nok", False):
                label, color, card_border = "NOK CENTRADO", "#f97316", "#92400e"
            else:
                label, color, card_border = "NOK AGUJEROS", _NOK, "#991b1b"

            miss_txt = f"  -  faltantes: {missing}" if missing else ""
            new_text = f"{label}{miss_txt}{center_txt}"
            new_card_state = f"{color}|{card_border}"

            self._result_lbl.setText(new_text)
            if new_card_state != self._last_card_state:
                self._result_lbl.setStyleSheet(f"font-size:12px;font-weight:700;color:{color};")
                self._result_card.setStyleSheet(
                    f"QFrame {{ background:{_PANEL};border:2px solid {card_border};"
                    "border-radius:8px; }}"
                )
                self._last_card_state = new_card_state
        else:
            if self._last_card_state != "none":
                self._result_lbl.setStyleSheet(f"font-size:12px;font-weight:700;color:{_MUTED};")
                self._result_card.setStyleSheet(
                    f"QFrame {{ background:{_PANEL};border:1px solid {_BORDER};"
                    "border-radius:8px; }}"
                )
                self._last_card_state = "none"
            self._result_lbl.setText("-")

        self._btn_save_current.setEnabled(idx < len(self._results))
        self._update_nav_state()

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._overlay_toggle.setText("OVERLAY ON" if checked else "OVERLAY OFF")
        self._show_frame(self._current_idx)

    def _update_nav_state(self) -> None:
        n = len(self._frame_paths)
        self._btn_first.setEnabled(self._current_idx > 0)
        self._btn_prev10.setEnabled(self._current_idx > 0)
        self._btn_prev.setEnabled(self._current_idx > 0)
        self._btn_next.setEnabled(self._current_idx < n - 1)
        self._btn_next10.setEnabled(self._current_idx < n - 1)
        self._btn_last.setEnabled(self._current_idx < n - 1)
        self._update_nok_nav_label()

    # ------------------------------------------------------------------
    # Save / Export
    # ------------------------------------------------------------------

    def _save_current_frame(self) -> None:
        """Auto-save current frame overlay to data/output/export/ with timestamp."""
        import cv2
        from datetime import datetime as _dt
        if self._current_idx >= len(self._results):
            return
        result = self._results[self._current_idx]
        bgr = self._result_bgr(self._current_idx)
        if bgr is None:
            return
        out_dir = Path("data/output/export")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts    = _dt.now().strftime("%Y%m%d_%H%M%S")
        fname = f"frame_{self._current_idx:04d}_{result.status}_{ts}.png"
        out_path = out_dir / fname
        cv2.imwrite(str(out_path), bgr)
        self._export_status_lbl.setText(f"OK  {fname}")
        logger.info(f"[Export] frame guardado -> {out_path}")

    def _update_export_label(self) -> None:
        if not self._frame_paths:
            return
        f_from = self._spin_from.value() - 1   # 0-based
        f_to   = self._spin_to.value()          # exclusive (1-based input = natural end)
        count  = max(0, f_to - f_from)
        has_results = len(self._results) >= f_to
        self._btn_export.setText(f"Exportar {count} frames")
        self._btn_export.setEnabled(count > 0 and has_results)

    def _update_export_range_max(self) -> None:
        """Sync spinbox range and export button when frame/result lists change."""
        n = len(self._frame_paths)
        self._spin_from.setRange(1, max(1, n))
        self._spin_to.setRange(1, max(1, n))
        if n > 0:
            self._spin_to.setValue(n)
        self._update_export_label()

    def _export_range(self) -> None:
        import cv2
        from datetime import datetime as _dt
        if not self._results:
            QMessageBox.information(self, "Exportar", "Primero analice la grabación.")
            return
        f_from = self._spin_from.value() - 1
        f_to   = min(self._spin_to.value(), len(self._results))
        if f_from >= f_to:
            return
        ts      = _dt.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("data/output/export") / f"rango_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(f_from, f_to):
            r = self._results[i]
            bgr = self._result_bgr(i)
            if bgr is not None:
                cv2.imwrite(str(out_dir / f"frame_{i:04d}_{r.status}.png"), bgr)
        saved = f_to - f_from
        self._export_status_lbl.setText(f"OK  {saved} frames -> export/rango_{ts}/")
        logger.info(f"[Export] {saved} frames -> {out_dir}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_model_chip(self, display_name: str = "") -> None:
        """Refresh the model-type chip in the browser nav bar."""
        name = display_name or self._model_combo.currentText()
        name_upper = name.upper()
        # Color-code by model family
        if "MICRO" in name_upper or "PERFOR" in name_upper:
            color, border = _ACCENT, "#0369a1"
        elif "ESTER" in name_upper:
            color, border = "#86efac", "#15803d"
        else:
            color, border = _MUTED, _BORDER
        self._model_chip.setText(name_upper)
        self._model_chip.setStyleSheet(
            f"color:{color};font-size:10px;font-weight:700;letter-spacing:2px;"
            f"background:{_DARK};border:1px solid {border};"
            "border-radius:7px;padding:0 14px;"
        )
        # Chip prominente al lado del botón Analizar (mismo color por tipo de placa).
        if hasattr(self, "_analyze_model_chip"):
            self._analyze_model_chip.setText(name_upper)
            self._analyze_model_chip.setStyleSheet(
                f"color:{color};font-size:13px;font-weight:800;letter-spacing:2px;"
                f"background:{_DARK};border:2px solid {border};"
                "border-radius:7px;padding:0 12px;"
            )

    def _set_rec_badge(self, state: str, n_frames: int,
                       folder: Optional[Path]) -> None:
        """Update the prominent recording state indicator."""
        _STATES = {
            "standby":   (f"color:{_MUTED};",   "STANDBY"),
            "recording": ("color:#f87171;",      "GRABANDO"),
            "ready":     (f"color:{_OK};",       "LISTO"),
            "analyzing": (f"color:{_ACCENT};",   "ANALIZANDO"),
            "analyzed":  (f"color:{_OK};",       "ANALIZADO"),
        }
        style, text = _STATES.get(state, (f"color:{_MUTED};", "-"))
        self._rec_state_lbl.setStyleSheet(
            f"{style}font-size:13px;font-weight:700;letter-spacing:3px;background:transparent;"
        )
        self._rec_state_lbl.setText(text)
        self._rec_count_lbl.setText(str(n_frames))
        self._rec_folder_lbl.setText(folder.name if folder else "-")

    def _refresh_cam_info(self) -> None:
        sid = self._scanner_combo.currentText()
        try:
            cam = self._system.camera(sid)
        except Exception:
            self._cam_info_lbl.setText("cámara no disponible")
            return
        _READ = [
            ("exp",   "exposure"),
            ("foc",   "focus"),
            ("gain",  "gain"),
            ("bri",   "brightness"),
            ("sharp", "sharpness"),
            ("fps",   "fps"),
        ]
        parts = []
        for lbl, key in _READ:
            v = cam.read_setting(key)
            parts.append(f"{lbl}:{v:.0f}" if v >= 0 else f"{lbl}:EUR")
        fps_real = cam.fps
        parts.append(f"real:{fps_real:.1f}" if fps_real > 0 else "real:-")
        self._cam_info_lbl.setText("  ".join(parts))

    def _active_model(self) -> str:
        return to_internal(self._model_combo.currentText())

    def _recording_model_label(self) -> str:
        name = self._model_combo.currentText().strip().upper()
        label = re.sub(r"[^A-Z0-9]+", "_", name).strip("_")
        return label or "SIN_MODELO"

    def _build_recording_folder_name(self, date_str: str) -> str:
        root = Path("data/recordings")
        model_label = self._recording_model_label()
        prefix = f"{date_str}-{model_label}_"
        next_idx = 1

        if root.exists():
            for path in root.iterdir():
                if not path.is_dir():
                    continue
                if not path.name.startswith(prefix):
                    continue
                suffix = path.name[len(prefix):]
                if suffix.isdigit():
                    next_idx = max(next_idx, int(suffix) + 1)

        return f"{date_str}-{model_label}_{next_idx}"

    def _unique_recording_dir(self, base_name: str) -> Path:
        root = Path("data/recordings")
        candidate = root / base_name
        if not candidate.exists():
            return candidate
        idx = 2
        while True:
            candidate = root / f"{base_name}_{idx:02d}"
            if not candidate.exists():
                return candidate
            idx += 1

    # ── Model toggle buttons ──────────────────────────────────────────

    _MODEL_BTN_STYLES = {
        "Esterilla": {
            "on":  ("background:#052e16;color:#4ade80;border-color:#16a34a;", "ESTERILLA"),
            "off": (f"background:#1e293b;color:#475569;border-color:#334155;", "ESTERILLA"),
        },
        "Microperforado": {
            "on":  ("background:#0c2a3e;color:#38bdf8;border-color:#0284c7;", "MICROPERFORADO"),
            "off": (f"background:#1e293b;color:#475569;border-color:#334155;", "MICROPERFORADO"),
        },
    }
    _MODEL_BTN_BASE = (
        "QPushButton {{ {style} border-radius:8px;font-size:12px;font-weight:700;"
        "  padding:0 18px;letter-spacing:1px; }}"
        "QPushButton:hover {{ border-color:#64748b; }}"
        "QPushButton:disabled {{ background:#131e2e;color:#374151;border-color:#1e293b; }}"
    )

    def _sync_model_buttons(self) -> None:
        current = self._model_combo.currentText()
        pairs = [
            ("Esterilla",      self._btn_model_esterilla),
            ("Microperforado", self._btn_model_microperf),
        ]
        if hasattr(self, "_btn_model_esterilla_ana"):
            pairs += [
                ("Esterilla",      self._btn_model_esterilla_ana),
                ("Microperforado", self._btn_model_microperf_ana),
            ]
        for name, btn in pairs:
            selected = (name == current)
            btn.blockSignals(True)
            btn.setChecked(selected)
            btn.blockSignals(False)
            style_str, label = self._MODEL_BTN_STYLES[name]["on" if selected else "off"]
            btn.setText(label)
            btn.setStyleSheet(self._MODEL_BTN_BASE.format(style=style_str))

    def _on_model_btn_toggled(self, name: str, checked: bool) -> None:
        if not checked:
            return
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentText(name)
        self._model_combo.blockSignals(False)
        self._sync_model_buttons()
        self._update_model_chip(name)
        # Forzar recarga del patrón/ROI en el próximo frame de análisis en vivo
        self._live_pre = None
        self._live_ms_detector = None
        self._live_session = None

    def _on_scanner_changed(self, sid: str) -> None:
        # El modelo NO cambia automáticamente al cambiar de scanner.
        # El operador puede elegir cualquier combinación de scanner + modelo,
        # por ejemplo analizar Esterilla grabada desde scanner_1.
        # Forzar recarga del patrón/ROI del nuevo scanner en el próximo frame
        self._live_pre = None
        self._live_ms_detector = None
        self._live_session = None
        self._auto_connect_scanner_camera(sid)
        self._update_fps_cap()

    def _sync_service_scanner_defaults(self, sid: str) -> None:
        """Align service model/scanner defaults with the active scanner config."""
        try:
            model_internal = str(self._system.io.scanner_config(sid).get("model", "")).strip()
            if model_internal:
                display = to_display(model_internal)
                if self._model_combo.currentText() != display:
                    self._model_combo.blockSignals(True)
                    self._model_combo.setCurrentText(display)
                    self._model_combo.blockSignals(False)
                    self._sync_model_buttons()
                    self._update_model_chip(display)
        except Exception:
            pass
        if hasattr(self, "_ana_scanner_combo") and self._ana_scanner_combo.currentText() != sid:
            self._ana_scanner_combo.blockSignals(True)
            self._ana_scanner_combo.setCurrentText(sid)
            self._ana_scanner_combo.blockSignals(False)

    def _update_fps_cap(self) -> None:
        """Limita el máximo del spinbox de FPS al FPS real medido de la cámara."""
        try:
            sid = self._scanner_combo.currentText()
            real_fps = self._system.camera(sid).fps
        except Exception:
            return
        if real_fps < 0.5:
            return  # aún no hay medición estable — no tocar el rango
        cap = max(1, int(real_fps))
        self._fps_spin.setMaximum(cap)
        if self._fps_spin.value() > cap:
            self._fps_spin.setValue(cap)
        self._fps_spin.setToolTip(f"FPS real de la cámara: {real_fps:.1f}")

    def _auto_connect_scanner_camera(self, sid: str) -> None:
        """Conecta la preview IP usando la camera_source del scanner seleccionado."""
        try:
            cfg = self._system.io.scanner_config(sid)
            url = str(cfg.get("camera_source", "")).strip()
        except Exception:
            url = ""
        if not url:
            return
        # Leer credenciales de camera.yaml para este scanner
        try:
            settings = camera_config.load_camera_settings(sid)
            user = settings.get("username") or None
            pwd  = settings.get("password") or None
        except Exception:
            user, pwd = None, None
        # Poblar campo URL y conectar
        if hasattr(self, "_ip_url_edit"):
            self._ip_url_edit.setText(url)
        self._on_ip_disconnect()
        self._ip_status_lbl.setText("Conectando...")
        if url.lower().startswith(("http://", "https://")):
            url_l = url.lower()
            if any(tok in url_l for tok in (".jpg", ".jpeg", "oneshot", "snapshot")):
                worker = _HTTPSnapshotReader(url, self, username=user, password=pwd)
            else:
                worker = _MJPEGReader(url, self, username=user, password=pwd)
            worker.frame_ready_meta.connect(lambda f, _m: self._on_ip_frame_ready(f))
            worker.error_occurred.connect(self._on_ip_error)
            self._ip_worker = worker
            worker.start()
        self._btn_ip_connect.setEnabled(False)
        self._btn_ip_disconnect.setEnabled(True)

    def _on_load_recording(self) -> None:
        """Load an existing recording folder for analysis."""
        from src.patterns.pattern_io import infer_scanner_id

        base = str(Path("data/recordings").resolve())
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar grabación", base,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        folder_path = Path(folder)
        frames = sorted(
            list(folder_path.glob("frame_*.png")) + list(folder_path.glob("frame_*.jpg"))
        )
        if not frames:
            self._status_lbl.setText("La carpeta no contiene frames (frame_*.png/jpg)")
            return

        self._rec_dir      = folder_path
        self._frame_paths  = frames

        inferred_scanner = infer_scanner_id(self._active_model(), folder_path)
        if inferred_scanner:
            if hasattr(self, "_scanner_combo") and self._scanner_combo.currentText() != inferred_scanner:
                self._scanner_combo.setCurrentText(inferred_scanner)
            if hasattr(self, "_ana_scanner_combo") and self._ana_scanner_combo.currentText() != inferred_scanner:
                self._ana_scanner_combo.setCurrentText(inferred_scanner)
            logger.info(f"[Grabación] scanner inferido desde carpeta: {inferred_scanner}")

        self._results.clear()
        self._nok_indices  = []
        self._current_idx  = 0
        self._px_cache.clear()
        self._last_card_state = None
        self._nok_nav_lbl.setText("NOK -")
        self._btn_prev_nok.setEnabled(False)
        self._btn_next_nok.setEnabled(False)
        self._summary_lbl.setText("")
        self._stats_lbl.setText("")
        self._ana_progress.setText("")
        self._export_status_lbl.setText("")

        meta_path = folder_path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                # NOTA: NO forzar el modelo desde meta.json. El operador elige el
                # modelo con los botones Esterilla/Microperforado y esa selección
                # debe mandar (una grabación puede estar mal etiquetada o querer
                # reanalizarse con otro patrón). meta.model_display es solo informativo.
                model_display = meta.get("model_display", "")
                if model_display and model_display != self._model_combo.currentText():
                    logger.info(
                        f"[Grabación] meta indica modelo '{model_display}' pero se "
                        f"respeta la selección actual '{self._model_combo.currentText()}'"
                    )
                fps_saved = meta.get("fps")
                if fps_saved:
                    self._fps_spin.setValue(fps_saved)
                scanner_saved = meta.get("scanner", "")
                if scanner_saved and hasattr(self, "_ana_scanner_combo"):
                    idx = self._ana_scanner_combo.findText(scanner_saved)
                    if idx >= 0:
                        self._ana_scanner_combo.setCurrentIndex(idx)
                        logger.info(f"[Grabación] scanner desde meta.json: {scanner_saved}")
                logger.info(f"[Grabación] meta cargada: {meta}")
            except Exception as exc:
                logger.warning(f"[Grabación] no se pudo leer meta.json: {exc}")

        self._btn_analyze.setEnabled(True)
        self._update_export_range_max()
        self._show_frame(0)
        self._set_rec_badge("ready", len(frames), folder_path)
        self._status_lbl.setText(f"Cargado - {len(frames)} frames  ·  {folder_path.name}")
        logger.info(f"[Grabación] cargada carpeta {folder_path.name} con {len(frames)} frames")

    def _mk_btn(self, text: str, bg: str, h: int = 30,
                fs: int = 11, w: int | None = None) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(h)
        if w is not None:
            b.setFixedWidth(w)
        # Compute a brighter hover variant (+30 lightness)
        b.setStyleSheet(
            f"QPushButton {{"
            f"  background:{bg};color:white;border-radius:6px;"
            f"  font-size:{fs}px;font-weight:700;border:none;padding:0 12px;"
            f"}}"
            f"QPushButton:hover {{ background-color: rgba(255,255,255,0.12);"
            f"  border:1px solid rgba(255,255,255,0.18); }}"
            f"QPushButton:pressed {{ background-color: rgba(0,0,0,0.25); }}"
            f"QPushButton:disabled {{ background:#1e293b;color:#475569;border:none; }}"
        )
        return b

    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color:{_MUTED};font-size:11px;font-weight:600;background:transparent;")
        return l

    def _make_combo(self, items: list, min_w: int = 110) -> QComboBox:
        c = QComboBox()
        c.addItems(items)
        c.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            f"border-radius:5px;padding:4px 8px;font-size:12px;min-width:{min_w}px;"
        )
        return c

    def _hline(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color:{_BORDER};max-height:1px;")
        return f

    def _vline(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        f.setStyleSheet(f"color:{_BORDER};")
        return f

    # ------------------------------------------------------------------
    # NOK navigation
    # ------------------------------------------------------------------

    def _rebuild_nok_index(self) -> None:
        self._nok_indices = [i for i, r in enumerate(self._results) if r.status == "NOK"]
        has_nok = bool(self._nok_indices)
        self._btn_prev_nok.setEnabled(has_nok)
        self._btn_next_nok.setEnabled(has_nok)
        total = len(self._nok_indices)
        self._nok_nav_lbl.setText(f"NOK {total}" if total else "NOK -")

    def _go_prev_nok(self) -> None:
        if not self._nok_indices:
            return
        candidates = [i for i in self._nok_indices if i < self._current_idx]
        target = candidates[-1] if candidates else self._nok_indices[-1]
        self._show_frame(target)

    def _go_next_nok(self) -> None:
        if not self._nok_indices:
            return
        candidates = [i for i in self._nok_indices if i > self._current_idx]
        target = candidates[0] if candidates else self._nok_indices[0]
        self._show_frame(target)

    def _update_nok_nav_label(self) -> None:
        if not self._nok_indices:
            return
        total = len(self._nok_indices)
        # Find position of current frame in NOK list (1-based), or nearest
        if self._current_idx in self._nok_indices:
            pos = self._nok_indices.index(self._current_idx) + 1
            self._nok_nav_lbl.setText(f"NOK {pos}/{total}")
        else:
            self._nok_nav_lbl.setText(f"NOK {total}")

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if obj is self._img_view and event.type() == QEvent.Type.KeyPress:
            self.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        key  = event.key()
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        if not self._frame_paths:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Right and ctrl:
            self._go_next_nok()
        elif key == Qt.Key.Key_Left and ctrl:
            self._go_prev_nok()
        elif key == Qt.Key.Key_Right:
            self._show_frame(self._current_idx + 1)
        elif key == Qt.Key.Key_Left:
            self._show_frame(self._current_idx - 1)
        else:
            super().keyPressEvent(event)

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:10px;margin-top:14px;padding-top:12px;"
            f"font-size:11px;font-weight:700;color:{_ACCENT};letter-spacing:2px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:14px;padding:0 6px; }}"
        )


# ==================================================================
# Evidencias / Eventos
# ==================================================================

class _EvOverlayWorker(QThread):
    """Corre inspect_image en background y emite (idx, QPixmap) con el overlay."""
    done = pyqtSignal(int, QPixmap)

    def __init__(self, idx: int, path: Path, model: str, scanner_id: str, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._path = path
        self._model = model
        self._scanner_id = scanner_id

    def run(self):
        try:
            from src.inspection import inspect_image
            result = inspect_image(self._model, str(self._path),
                                   scanner_id=self._scanner_id)
            ov = result.overlay
            if ov is not None:
                rgb = cv2.cvtColor(ov, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                qi = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
                self.done.emit(self._idx, QPixmap.fromImage(qi))
        except Exception:
            pass


@dataclass
class _EventEntry:
    folder: Path
    name: str
    event_type: str
    scanner_id: str
    reason: str
    frame_count: int
    total_bytes: int
    event_dt: datetime | None
    has_manifest: bool


class EventBrowserTab(QWidget):
    """Explorador de evidencias guardadas en data/events/ con carga bajo demanda."""

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._events_dir = _ROOT / "data" / "events"
        self._max_budget_bytes = 10 * 1_000_000_000
        self._entries: list[_EventEntry] = []
        self._filtered_entries: list[_EventEntry] = []
        self._frame_paths: list[Path] = []
        self._current_entry: _EventEntry | None = None
        self._current_idx = 0
        self._px_cache: dict[int, QPixmap] = {}
        self._px_cache_max = 18
        self._show_overlay: bool = False
        self._ov_cache: dict[int, QPixmap] = {}
        self._overlay_worker: Optional[_EvOverlayWorker] = None
        self._build_ui()
        self._refresh_events()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addWidget(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_list_section(), stretch=4)
        body.addWidget(self._build_viewer_section(), stretch=7)
        root.addLayout(body, stretch=1)

    def _build_toolbar(self) -> QGroupBox:
        grp = QGroupBox("BIBLIOTECA DE EVIDENCIAS")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self._btn_refresh_events = self._mk_btn("Actualizar", "#1d4ed8", h=36, fs=11)
        self._btn_latest_event = self._mk_btn("Ir al último", "#0f766e", h=36, fs=11)
        self._btn_open_folder = self._mk_btn("Abrir carpeta", "#374151", h=36, fs=11)
        self._btn_delete_event = self._mk_btn("Borrar evento", "#991b1b", h=36, fs=11)
        self._btn_delete_frame = self._mk_btn("Borrar frame", "#7f1d1d", h=36, fs=11)
        self._btn_open_folder.setEnabled(False)
        self._btn_delete_event.setEnabled(False)
        self._btn_delete_frame.setEnabled(False)
        row1.addWidget(self._btn_refresh_events)
        row1.addWidget(self._btn_latest_event)
        row1.addWidget(self._btn_open_folder)
        row1.addWidget(self._btn_delete_event)
        row1.addWidget(self._btn_delete_frame)
        row1.addSpacing(12)

        self._storage_lbl = self._metric_badge("Uso: 0 B / 10.0 GB", _TEXT, _DARK, _BORDER)
        self._events_count_lbl = self._metric_badge("Eventos: 0", _ACCENT, _DARK, _BORDER)
        self._frames_count_lbl = self._metric_badge("Frames: 0", _OK, _DARK, _BORDER)
        row1.addWidget(self._storage_lbl)
        row1.addWidget(self._events_count_lbl)
        row1.addWidget(self._frames_count_lbl)
        row1.addStretch()

        self._events_status_lbl = QLabel("Listo")
        self._events_status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
        )
        row1.addWidget(self._events_status_lbl)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self._scanner_filter = self._make_combo(["Todos"] + self._system.scanner_ids(), min_w=120)
        self._type_filter = self._make_combo(
            ["Todos", "machine_stop", "fault", "sin manifest"], min_w=135
        )
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Buscar por nombre, motivo o scanner...")
        self._search_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:6px;padding:7px 10px;font-size:12px;"
        )
        row2.addWidget(self._filter_chip("SCANNER"))
        row2.addWidget(self._scanner_filter)
        row2.addWidget(self._filter_chip("TIPO"))
        row2.addWidget(self._type_filter)
        row2.addWidget(self._filter_chip("BUSCAR"))
        row2.addWidget(self._search_edit, stretch=1)
        lay.addLayout(row2)

        self._btn_refresh_events.clicked.connect(self._refresh_events)
        self._btn_latest_event.clicked.connect(self._go_to_latest)
        self._btn_open_folder.clicked.connect(self._open_current_folder)
        self._btn_delete_event.clicked.connect(self._delete_current_event)
        self._btn_delete_frame.clicked.connect(self._delete_current_frame)
        self._scanner_filter.currentTextChanged.connect(self._apply_filters)
        self._type_filter.currentTextChanged.connect(self._apply_filters)
        self._search_edit.textChanged.connect(self._apply_filters)
        return grp

    def _build_list_section(self) -> QGroupBox:
        grp = QGroupBox("EVENTOS DISPONIBLES")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(8)

        self._events_table = QTableWidget(0, 6)
        self._events_table.setHorizontalHeaderLabels(
            ["Evento", "Tipo", "Scanner", "Hora", "Frames", "Tamaño"]
        )
        self._events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._events_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._events_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._events_table.verticalHeader().setVisible(False)
        self._events_table.horizontalHeader().setStretchLastSection(False)
        self._events_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4, 5):
            self._events_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._events_table.setStyleSheet(
            f"QTableWidget {{ background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:8px;gridline-color:#162234;font-size:11px; }}"
            f"QHeaderView::section {{ background:{_PANEL};color:{_MUTED};border:none;"
            "padding:6px;font-size:10px;font-weight:700; }}"
            f"QTableWidget::item:selected {{ background:#0c4a6e;color:{_TEXT}; }}"
        )
        self._events_table.currentCellChanged.connect(self._on_event_row_changed)
        lay.addWidget(self._events_table)
        return grp

    def _build_viewer_section(self) -> QGroupBox:
        grp = QGroupBox("VISOR DE EVIDENCIAS")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(10)

        self._event_title_lbl = QLabel("Seleccione una evidencia")
        self._event_title_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:15px;font-weight:700;letter-spacing:1px;"
        )
        lay.addWidget(self._event_title_lbl)

        self._event_meta_lbl = QLabel("Sin datos")
        self._event_meta_lbl.setWordWrap(True)
        self._event_meta_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:8px;padding:8px 10px;"
        )
        lay.addWidget(self._event_meta_lbl)

        lay.addLayout(self._build_nav_bar())

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setEnabled(False)
        self._frame_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ background:{_DARK};height:6px;border-radius:3px; }}"
            f"QSlider::sub-page:horizontal {{ background:{_ACCENT};border-radius:3px; }}"
            f"QSlider::handle:horizontal {{ background:{_TEXT};width:14px;margin:-5px 0;"
            "border-radius:7px; }}"
        )
        self._frame_slider.valueChanged.connect(self._on_slider_changed)
        lay.addWidget(self._frame_slider)

        self._frame_file_lbl = QLabel("-")
        self._frame_file_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
        )
        lay.addWidget(self._frame_file_lbl)

        self._event_img_view = ZoomableImageView("Seleccione un evento para ver sus imágenes")
        lay.addWidget(self._event_img_view, stretch=1)
        return grp

    def _build_nav_bar(self) -> QHBoxLayout:
        nav = QHBoxLayout()
        nav.setSpacing(4)

        _nav_base = (
            "QPushButton {{ background:{bg};color:{fg};border-radius:7px;"
            "font-size:{fs}px;font-weight:700;border:1px solid {bd};padding:0 {pad}px; }}"
            "QPushButton:hover {{ background:{hv};border-color:#64748b; }}"
            "QPushButton:pressed {{ background:#0f172a; }}"
            "QPushButton:disabled {{ background:#131e2e;color:#374151;border-color:#1e293b; }}"
        )

        def _nav_btn(label: str, bg="#1e293b", fg=_TEXT, bd=_BORDER,
                     hv="#2d3f55", fs=12, w=None, pad=8):
            btn = QPushButton(label)
            btn.setFixedHeight(38)
            if w is not None:
                btn.setFixedWidth(w)
            btn.setStyleSheet(_nav_base.format(
                bg=bg, fg=fg, bd=bd, hv=hv, fs=fs, pad=pad
            ))
            return btn

        self._btn_event_first = _nav_btn("|<", w=40, pad=0)
        self._btn_event_prev10 = _nav_btn("<<  -10", w=74, fs=11)
        self._btn_event_prev = _nav_btn("<  Ant.", w=76, bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8")
        self._event_nav_lbl = QLabel("-")
        self._event_nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._event_nav_lbl.setMinimumWidth(120)
        self._event_nav_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:15px;font-weight:700;background:{_DARK};"
            f"border:1px solid {_BORDER};border-radius:7px;padding:4px 16px;letter-spacing:2px;"
        )
        self._btn_event_next = _nav_btn("Sig.  >", w=76, bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8")
        self._btn_event_next10 = _nav_btn("+10  >>", w=74, fs=11)
        self._btn_event_last = _nav_btn(">|", w=40, pad=0)
        self._btn_event_fit = _nav_btn("Ajustar", w=86, bg="#0f766e", bd="#0f766e", hv="#0d9488", fs=11)
        self._btn_event_overlay = _nav_btn("OVERLAY", w=96, fs=11)
        self._btn_event_overlay.setCheckable(True)
        self._btn_event_overlay.setChecked(False)
        self._btn_event_overlay.setStyleSheet(
            self._btn_event_overlay.styleSheet()
            + f"QPushButton:checked {{ background:{_ACCENT}; color:#ffffff; border-color:{_ACCENT}; }}"
        )

        for btn in (
            self._btn_event_first, self._btn_event_prev10, self._btn_event_prev,
            self._btn_event_next, self._btn_event_next10, self._btn_event_last,
        ):
            btn.setEnabled(False)

        self._btn_event_first.clicked.connect(lambda: self._show_event_frame(0))
        self._btn_event_prev10.clicked.connect(lambda: self._show_event_frame(self._current_idx - 10))
        self._btn_event_prev.clicked.connect(lambda: self._show_event_frame(self._current_idx - 1))
        self._btn_event_next.clicked.connect(lambda: self._show_event_frame(self._current_idx + 1))
        self._btn_event_next10.clicked.connect(lambda: self._show_event_frame(self._current_idx + 10))
        self._btn_event_last.clicked.connect(lambda: self._show_event_frame(len(self._frame_paths) - 1))
        self._btn_event_fit.clicked.connect(lambda: self._event_img_view.fit())
        self._btn_event_overlay.clicked.connect(self._on_overlay_toggled)

        # Scanner selector para análisis overlay
        _sc_lbl = QLabel("Scanner:")
        _sc_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;font-weight:600;")
        self._overlay_scanner_combo = self._make_combo(
            self._system.scanner_ids(), min_w=110
        )
        self._overlay_scanner_combo.currentTextChanged.connect(self._on_overlay_scanner_changed)

        nav.addWidget(self._btn_event_first)
        nav.addWidget(self._btn_event_prev10)
        nav.addWidget(self._btn_event_prev)
        nav.addSpacing(6)
        nav.addWidget(self._event_nav_lbl)
        nav.addSpacing(6)
        nav.addWidget(self._btn_event_next)
        nav.addWidget(self._btn_event_next10)
        nav.addWidget(self._btn_event_last)
        nav.addSpacing(10)
        nav.addWidget(self._btn_event_fit)
        nav.addWidget(self._btn_event_overlay)
        nav.addSpacing(8)
        nav.addWidget(_sc_lbl)
        nav.addWidget(self._overlay_scanner_combo)
        nav.addStretch()
        return nav

    def _refresh_events(self) -> None:
        current_name = self._current_entry.name if self._current_entry else None
        self._entries = self._scan_event_entries()
        self._apply_filters()
        self._update_storage_badges()

        restored = False
        if current_name:
            for idx, entry in enumerate(self._filtered_entries):
                if entry.name == current_name:
                    self._events_table.selectRow(idx)
                    self._load_event(entry)
                    restored = True
                    break

        if self._filtered_entries and not restored:
            self._events_table.selectRow(0)
            self._load_event(self._filtered_entries[0])
        elif not self._filtered_entries:
            self._clear_event_selection("No hay evidencias guardadas")

        self._events_status_lbl.setText(
            f"Actualizado {datetime.now().strftime('%H:%M:%S')}"
        )

    def reload(self) -> None:
        self._refresh_events()

    def _scan_event_entries(self) -> list[_EventEntry]:
        if not self._events_dir.exists():
            return []

        entries: list[_EventEntry] = []
        for folder in self._events_dir.iterdir():
            if not folder.is_dir():
                continue
            manifest_path = folder / "manifest.json"
            manifest: dict = {}
            has_manifest = manifest_path.exists()
            if has_manifest:
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning(f"[Evidencias] manifest inválido en {folder.name}: {exc}")

            frames = self._discover_event_frames(folder)
            total_bytes = self._dir_size(folder)
            event_dt = None
            raw_ts = manifest.get("timestamp")
            if isinstance(raw_ts, str):
                try:
                    event_dt = datetime.fromisoformat(raw_ts)
                except Exception:
                    event_dt = None
            if event_dt is None:
                try:
                    event_dt = datetime.fromtimestamp(folder.stat().st_mtime)
                except Exception:
                    event_dt = None

            manifest_frames = self._safe_int(manifest.get("frames_count"))
            if manifest_frames <= 0:
                manifest_frames = (
                    self._safe_int(manifest.get("pre_frames_count"))
                    + self._safe_int(manifest.get("post_frames_count"))
                )
            if manifest_frames <= 0:
                manifest_frames = len(frames)

            entries.append(_EventEntry(
                folder=folder,
                name=folder.name,
                event_type=str(manifest.get("event_type") or ("sin manifest" if not has_manifest else "-")),
                scanner_id=str(manifest.get("scanner_id") or "-"),
                reason=str(manifest.get("reason") or ""),
                frame_count=manifest_frames,
                total_bytes=total_bytes,
                event_dt=event_dt,
                has_manifest=has_manifest,
            ))

        return sorted(
            entries,
            key=lambda entry: entry.event_dt or datetime.min,
            reverse=True,
        )

    def _discover_event_frames(self, folder: Path) -> list[Path]:
        patterns = ("*.jpg", "*.jpeg", "*.png", "*.bmp")
        frames: list[Path] = []
        for pattern in patterns:
            frames.extend(folder.rglob(pattern))
        return sorted(path for path in frames if path.is_file())

    def _apply_filters(self) -> None:
        scanner = self._scanner_filter.currentText()
        ev_type = self._type_filter.currentText()
        term = self._search_edit.text().strip().lower()

        def _match(entry: _EventEntry) -> bool:
            if scanner != "Todos" and entry.scanner_id != scanner:
                return False
            if ev_type != "Todos" and entry.event_type != ev_type:
                return False
            if term:
                hay = " ".join([entry.name, entry.scanner_id, entry.event_type, entry.reason]).lower()
                if term not in hay:
                    return False
            return True

        self._filtered_entries = [entry for entry in self._entries if _match(entry)]
        self._populate_events_table()

    def _populate_events_table(self) -> None:
        self._events_table.blockSignals(True)
        self._events_table.setRowCount(len(self._filtered_entries))
        for row, entry in enumerate(self._filtered_entries):
            hour_txt = entry.event_dt.strftime("%H:%M:%S") if entry.event_dt else "-"
            values = [
                entry.name,
                entry.event_type,
                entry.scanner_id,
                hour_txt,
                str(entry.frame_count),
                self._human_bytes(entry.total_bytes),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._events_table.setItem(row, col, item)
        self._events_table.blockSignals(False)

    def _on_event_row_changed(self, current_row: int, _current_col: int,
                              _prev_row: int, _prev_col: int) -> None:
        if 0 <= current_row < len(self._filtered_entries):
            self._load_event(self._filtered_entries[current_row])

    def _load_event(self, entry: _EventEntry) -> None:
        self._current_entry = entry
        self._frame_paths = self._discover_event_frames(entry.folder)
        self._current_idx = 0
        self._px_cache.clear()
        self._ov_cache.clear()
        if self._overlay_worker is not None and self._overlay_worker.isRunning():
            self._overlay_worker.done.disconnect()
            self._overlay_worker.quit()

        self._event_title_lbl.setText(entry.name)
        dt_txt = entry.event_dt.strftime("%d-%m-%Y %H:%M:%S") if entry.event_dt else "-"
        self._event_meta_lbl.setText(
            "\n".join([
                f"Fecha:      {dt_txt}",
                f"Scanner:    {entry.scanner_id}",
                f"Tipo:       {entry.event_type}",
                f"Frames:     {len(self._frame_paths)}",
                f"Tamaño:     {self._human_bytes(entry.total_bytes)}",
                f"Manifest:   {'Sí' if entry.has_manifest else 'No'}",
                f"Motivo:     {entry.reason or '-'}",
            ])
        )

        # Sincronizar combo de scanner con el del evento (sin disparar recarga)
        if entry.scanner_id in self._system.scanner_ids():
            self._overlay_scanner_combo.blockSignals(True)
            self._overlay_scanner_combo.setCurrentText(entry.scanner_id)
            self._overlay_scanner_combo.blockSignals(False)

        self._btn_open_folder.setEnabled(True)
        self._btn_delete_event.setEnabled(True)
        self._btn_delete_frame.setEnabled(bool(self._frame_paths))

        if self._frame_paths:
            self._frame_slider.blockSignals(True)
            self._frame_slider.setEnabled(True)
            self._frame_slider.setRange(0, len(self._frame_paths) - 1)
            self._frame_slider.setValue(0)
            self._frame_slider.blockSignals(False)
            self._show_event_frame(0)
        else:
            self._frame_slider.blockSignals(True)
            self._frame_slider.setEnabled(False)
            self._frame_slider.setRange(0, 0)
            self._frame_slider.setValue(0)
            self._frame_slider.blockSignals(False)
            self._event_img_view.clear("La carpeta no contiene imágenes")
            self._event_nav_lbl.setText("0 / 0")
            self._frame_file_lbl.setText("Sin frames")
            self._update_event_nav_state()

    def _show_event_frame(self, idx: int) -> None:
        if not self._frame_paths:
            return
        idx = max(0, min(idx, len(self._frame_paths) - 1))
        self._current_idx = idx

        # ── Overlay mode ──────────────────────────────────────────────
        if self._show_overlay and self._current_entry is not None:
            ov_pxm = self._ov_cache.get(idx)
            sc_sel = self._overlay_scanner_combo.currentText()
            if ov_pxm is not None:
                prev = self._event_img_view.current_pixmap()
                needs_fit = prev is None or prev.size() != ov_pxm.size()
                self._event_img_view.set_pixmap(ov_pxm, auto_fit=needs_fit)
                self._events_status_lbl.setText(
                    f"Overlay · {sc_sel} · {self._scanner_model(sc_sel)}"
                )
            else:
                # Mostrar frame crudo mientras se analiza
                raw_pxm = self._load_raw_pixmap(idx)
                if raw_pxm is not None:
                    prev = self._event_img_view.current_pixmap()
                    needs_fit = prev is None or prev.size() != raw_pxm.size()
                    self._event_img_view.set_pixmap(raw_pxm, auto_fit=needs_fit)
                self._events_status_lbl.setText(
                    f"Analizando con {sc_sel} · {self._scanner_model(sc_sel)}…"
                )
                self._launch_overlay_worker(idx)
            self._event_nav_lbl.setText(f"{idx + 1} / {len(self._frame_paths)}")
            self._frame_file_lbl.setText(self._frame_paths[idx].name)
            self._frame_slider.blockSignals(True)
            self._frame_slider.setValue(idx)
            self._frame_slider.blockSignals(False)
            self._update_event_nav_state()
            return

        # ── Raw mode ──────────────────────────────────────────────────
        pxm = self._load_raw_pixmap(idx)
        if pxm is not None:
            prev = self._event_img_view.current_pixmap()
            needs_fit = prev is None or prev.size() != pxm.size()
            self._event_img_view.set_pixmap(pxm, auto_fit=needs_fit)
            self._events_status_lbl.setText(
                f"Mostrando {self._current_entry.name if self._current_entry else '-'}"
            )
        else:
            self._event_img_view.clear("No se pudo leer la imagen seleccionada")
            self._events_status_lbl.setText(f"Frame ilegible: {self._frame_paths[idx].name}")

        self._event_nav_lbl.setText(f"{idx + 1} / {len(self._frame_paths)}")
        self._frame_file_lbl.setText(self._frame_paths[idx].name)
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)
        self._update_event_nav_state()

    def _load_raw_pixmap(self, idx: int) -> Optional[QPixmap]:
        pxm = self._px_cache.get(idx)
        if pxm is None:
            bgr = cv2.imread(str(self._frame_paths[idx]))
            if bgr is None:
                return None
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            qi = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
            pxm = QPixmap.fromImage(qi)
            self._px_cache[idx] = pxm
            if len(self._px_cache) > self._px_cache_max:
                keys = sorted(self._px_cache.keys(), key=lambda key: abs(key - idx), reverse=True)
                for key in keys[self._px_cache_max // 2:]:
                    del self._px_cache[key]
        return pxm

    def _launch_overlay_worker(self, idx: int) -> None:
        if self._overlay_worker is not None and self._overlay_worker.isRunning():
            self._overlay_worker.done.disconnect()
            self._overlay_worker.quit()
        scanner_id = self._overlay_scanner_combo.currentText()
        model = self._scanner_model(scanner_id)
        worker = _EvOverlayWorker(idx, self._frame_paths[idx], model, scanner_id)
        worker.done.connect(self._on_overlay_done)
        self._overlay_worker = worker
        worker.start()

    def _on_overlay_done(self, idx: int, pxm: QPixmap) -> None:
        self._ov_cache[idx] = pxm
        if idx == self._current_idx and self._show_overlay:
            prev = self._event_img_view.current_pixmap()
            needs_fit = prev is None or prev.size() != pxm.size()
            self._event_img_view.set_pixmap(pxm, auto_fit=needs_fit)
            scanner_id = self._overlay_scanner_combo.currentText()
            model = self._scanner_model(scanner_id)
            self._events_status_lbl.setText(f"Overlay · {scanner_id} · {model}")

    def _on_overlay_toggled(self) -> None:
        self._show_overlay = self._btn_event_overlay.isChecked()
        self._show_event_frame(self._current_idx)

    def _on_overlay_scanner_changed(self, _scanner_id: str) -> None:
        """Al cambiar el scanner para análisis: invalidar cache de overlays y re-analizar."""
        self._ov_cache.clear()
        if self._show_overlay:
            self._show_event_frame(self._current_idx)

    def _scanner_model(self, scanner_id: str) -> str:
        try:
            import yaml
            cfg = yaml.safe_load((_ROOT / "config" / "io_map.yaml").read_text(encoding="utf-8"))
            return cfg.get(scanner_id, {}).get("model", "modelo_A")
        except Exception:
            return "modelo_A"

    def _on_slider_changed(self, value: int) -> None:
        self._show_event_frame(value)

    def _update_event_nav_state(self) -> None:
        n = len(self._frame_paths)
        enabled = n > 0
        self._btn_event_first.setEnabled(enabled and self._current_idx > 0)
        self._btn_event_prev10.setEnabled(enabled and self._current_idx > 0)
        self._btn_event_prev.setEnabled(enabled and self._current_idx > 0)
        self._btn_event_next.setEnabled(enabled and self._current_idx < n - 1)
        self._btn_event_next10.setEnabled(enabled and self._current_idx < n - 1)
        self._btn_event_last.setEnabled(enabled and self._current_idx < n - 1)
        self._btn_event_fit.setEnabled(enabled)

    def _go_to_latest(self) -> None:
        if not self._filtered_entries:
            return
        self._events_table.selectRow(0)
        self._load_event(self._filtered_entries[0])

    def _open_current_folder(self) -> None:
        if self._current_entry is None:
            return
        try:
            os.startfile(str(self._current_entry.folder))
        except Exception as exc:
            QMessageBox.warning(self, "Abrir carpeta", f"No se pudo abrir la carpeta:\n{exc}")

    def _delete_current_event(self) -> None:
        if self._current_entry is None:
            return
        name = self._current_entry.name
        answer = QMessageBox.question(
            self,
            "Borrar evidencia",
            f"¿Desea borrar la evidencia '{name}'?\nEsta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(self._current_entry.folder)
            logger.info(f"[Evidencias] eliminada {name}")
            self._refresh_events()
        except Exception as exc:
            QMessageBox.warning(self, "Borrar evidencia", f"No se pudo borrar:\n{exc}")

    def _delete_current_frame(self) -> None:
        if not self._frame_paths or self._current_idx >= len(self._frame_paths):
            return
        path = self._frame_paths[self._current_idx]
        answer = QMessageBox.question(
            self, "Borrar frame",
            f"¿Borrar '{path.name}'?\nEsta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink(missing_ok=True)
            logger.info(f"[Evidencias] frame eliminado: {path.name}")
            self._frame_paths.pop(self._current_idx)
            self._px_cache.pop(self._current_idx, None)
            self._ov_cache.pop(self._current_idx, None)
            # Re-indexar caches
            self._px_cache = {(k if k < self._current_idx else k - 1): v
                              for k, v in self._px_cache.items()}
            self._ov_cache = {(k if k < self._current_idx else k - 1): v
                              for k, v in self._ov_cache.items()}
            if not self._frame_paths:
                self._event_img_view.clear("No hay más frames en este evento")
                self._event_nav_lbl.setText("0 / 0")
                self._frame_file_lbl.setText("-")
                self._frame_slider.blockSignals(True)
                self._frame_slider.setEnabled(False)
                self._frame_slider.setRange(0, 0)
                self._frame_slider.blockSignals(False)
                self._btn_delete_frame.setEnabled(False)
                self._update_event_nav_state()
                return
            self._frame_slider.blockSignals(True)
            self._frame_slider.setRange(0, len(self._frame_paths) - 1)
            self._frame_slider.blockSignals(False)
            next_idx = min(self._current_idx, len(self._frame_paths) - 1)
            self._show_event_frame(next_idx)
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")

    def _clear_event_selection(self, placeholder: str) -> None:
        self._current_entry = None
        self._frame_paths = []
        self._current_idx = 0
        self._px_cache.clear()
        self._btn_open_folder.setEnabled(False)
        self._btn_delete_event.setEnabled(False)
        self._btn_delete_frame.setEnabled(False)
        self._event_title_lbl.setText("Seleccione una evidencia")
        self._event_meta_lbl.setText("Sin datos")
        self._event_nav_lbl.setText("0 / 0")
        self._frame_file_lbl.setText("-")
        self._frame_slider.blockSignals(True)
        self._frame_slider.setEnabled(False)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        self._event_img_view.clear(placeholder)
        self._update_event_nav_state()

    def _update_storage_badges(self) -> None:
        used = self._dir_size(self._events_dir) if self._events_dir.exists() else 0
        total_frames = sum(entry.frame_count for entry in self._entries)
        pct = (used / self._max_budget_bytes) if self._max_budget_bytes > 0 else 0.0
        color = _OK if pct < 0.7 else (_WARN if pct < 0.9 else _NOK)
        self._storage_lbl.setText(
            f"Uso: {self._human_bytes(used)} / {self._human_bytes(self._max_budget_bytes)}"
        )
        self._storage_lbl.setStyleSheet(
            f"color:{color};background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:7px;padding:6px 12px;font-size:11px;font-weight:700;"
        )
        self._events_count_lbl.setText(f"Eventos: {len(self._entries)}")
        self._frames_count_lbl.setText(f"Frames: {total_frames}")

    def refresh(self) -> None:
        self._update_storage_badges()

    def keyPressEvent(self, event) -> None:
        if not self._frame_paths:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Right:
            self._show_event_frame(self._current_idx + 1)
        elif event.key() == Qt.Key.Key_Left:
            self._show_event_frame(self._current_idx - 1)
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())

    @staticmethod
    def _human_bytes(num: int) -> str:
        value = float(num)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024.0 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024.0
        return f"{num} B"

    @staticmethod
    def _filter_chip(label: str) -> QLabel:
        chip = QLabel(label)
        chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:4px;padding:2px 8px;"
        )
        return chip

    @staticmethod
    def _metric_badge(text: str, fg: str, bg: str, bd: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{fg};background:{bg};border:1px solid {bd};border-radius:7px;"
            "padding:6px 12px;font-size:11px;font-weight:700;"
        )
        return lbl

    def _mk_btn(self, text: str, bg: str, h: int = 30,
                fs: int = 11, w: int | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(h)
        if w is not None:
            btn.setFixedWidth(w)
        btn.setStyleSheet(
            f"QPushButton {{ background:{bg};color:white;border-radius:6px;"
            f"font-size:{fs}px;font-weight:700;border:none;padding:0 12px; }}"
            "QPushButton:hover { background-color: rgba(255,255,255,0.12);"
            "border:1px solid rgba(255,255,255,0.18); }"
            "QPushButton:pressed { background-color: rgba(0,0,0,0.25); }"
            "QPushButton:disabled { background:#1e293b;color:#475569;border:none; }"
        )
        return btn

    def _make_combo(self, items: list[str], min_w: int = 110) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            f"border-radius:5px;padding:4px 8px;font-size:12px;min-width:{min_w}px;"
        )
        return combo

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:10px;margin-top:14px;padding-top:12px;"
            f"font-size:11px;font-weight:700;color:{_ACCENT};letter-spacing:2px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:14px;padding:0 6px; }}"
        )


# ==================================================================
# Calibración de cámara
# ==================================================================

@dataclass
class _ParamDef:
    key:      str
    label:    str
    min_val:  int
    max_val:  int
    default:  int
    auto_key: str | None = None   # settings key for the matching boolean auto


_PARAM_DEFS: list[_ParamDef] = [
    _ParamDef("focus",                  "Foco",            0,    255, 100, "autofocus"),
    _ParamDef("exposure",               "Exposición",    -13,      0,  -7, "auto_exposure"),
    _ParamDef("white_balance",          "Bal. Blanco",  2800,   6500, 4500, "auto_white_balance"),
    _ParamDef("gain",                   "Ganancia",        0,    255,   0),
    _ParamDef("brightness",             "Brillo",          0,    255, 128),
    _ParamDef("contrast",               "Contraste",       0,    255, 140),
    _ParamDef("saturation",             "Saturación",      0,    255,  50),
    _ParamDef("sharpness",              "Nitidez",         0,    255, 160),
    _ParamDef("gamma",                  "Gamma",         100,    500, 110),
    _ParamDef("backlight_compensation", "Comp. backlight", 0,      1,   0),
]

class CameraCalibTab(QWidget):
    """Vista en vivo + sliders de todos los parámetros UVC de la cámara."""

    def __init__(self, system: InspectionSystem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._system = system
        self._sliders:     dict[str, QSlider]   = {}
        self._spinboxes:   dict[str, QSpinBox]  = {}
        self._auto_checks: dict[str, QCheckBox] = {}
        self._block_apply = False  # avoid feedback loops while populating UI

        # IP camera state - two independent slots
        self._ip_workers: list[Optional[QThread]] = [None, None]
        self._ip_caps:    list[Optional[cv2.VideoCapture]] = [None, None]
        self._ip_timers:  list[QTimer] = []
        for _i in range(2):
            _t = QTimer(self)
            _t.timeout.connect(lambda _s=_i: self._refresh_ip_camera(_s))
            self._ip_timers.append(_t)
        self._ip_slot = 0

        # Auto-reconectar, FPS y captura
        self._ip_retry_timers: list[QTimer] = []
        for _i in range(2):
            _rt = QTimer(self)
            _rt.setSingleShot(True)
            _rt.timeout.connect(lambda _s=_i: self._on_ip_retry(_s))
            self._ip_retry_timers.append(_rt)
        self._ip_retry_counts   = [0, 0]    # reintentos pendientes por slot
        self._ip_manual_disc    = [False, False]  # True = el operador desconectó manualmente
        self._ip_fps_count      = [0, 0]    # frames desde último cálculo de FPS
        self._ip_fps_last_t     = [0.0, 0.0]
        self._ip_fps_value      = [0.0, 0.0]
        self._ip_last_res       = ["", ""]  # "WxH" del último frame
        self._ip_last_frames: list = [None, None]  # último frame BGR por slot

        self._ip_last_frame_t = [0.0, 0.0]
        self._ip_dropped_frames = [0, 0]
        self._ip_reconnect_total = [0, 0]
        self._ip_prev_sig = [None, None]
        self._ip_frozen_since = [0.0, 0.0]
        self._ip_frozen = [False, False]
        self._ip_last_diag_snapshot_t = [0.0, 0.0]
        self._ip_auto_urls = ["", ""]

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.setInterval(200)

        self._build_ui()
        self._populate_scanner_selector()
        # Auto-conectar ambas cámaras IP al abrir la pestaña
        QTimer.singleShot(0, self._auto_connect_all_slots)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:{_DARK}; }}"
            f"QScrollBar:vertical {{ background:{_DARK};width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:{_BORDER};border-radius:4px;min-height:30px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        outer.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        # ── top bar ──────────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(10)

        lbl = QLabel("Scanner:")
        lbl.setStyleSheet(f"color:{_TEXT};font-size:12px;")
        top.addWidget(lbl)

        self._scanner_combo = QComboBox()
        self._scanner_combo.setFixedWidth(130)
        self._scanner_combo.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:4px 8px;font-size:12px;"
        )
        self._scanner_combo.currentTextChanged.connect(self._on_scanner_changed)
        top.addWidget(self._scanner_combo)

        self._cam_btn = QPushButton("Iniciar cámara")
        self._cam_btn.setFixedWidth(140)
        self._cam_btn.clicked.connect(self._toggle_camera)
        top.addWidget(self._cam_btn)

        self._read_btn = QPushButton("Leer de cámara")
        self._read_btn.setFixedWidth(140)
        self._read_btn.clicked.connect(self._read_from_camera)
        top.addWidget(self._read_btn)

        top.addStretch()

        self._status_lbl = QLabel("-")
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(self._status_lbl)

        root.addLayout(top)
        root.addWidget(self._build_ip_camera_section(), stretch=4)

        # ── main split ───────────────────────────────────────────────
        main = QHBoxLayout()
        main.setSpacing(12)

        # Left: live preview
        prev_grp = QGroupBox("Vista en vivo")
        prev_grp.setStyleSheet(self._grp_style())
        prev_grp.setMinimumWidth(560)
        prev_lay = QVBoxLayout(prev_grp)

        self._preview_lbl = QLabel("Sin señal de cámara")
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setMinimumSize(540, 340)
        self._preview_lbl.setStyleSheet(
            f"background:{_DARK};color:{_MUTED};border-radius:6px;font-size:13px;"
        )
        prev_lay.addWidget(self._preview_lbl, stretch=1)

        self._fps_lbl = QLabel("")
        self._fps_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fps_lbl.setStyleSheet(f"color:{_MUTED};font-size:10px;")
        prev_lay.addWidget(self._fps_lbl)

        main.addWidget(prev_grp, stretch=3)

        # Right: parameter panel
        params_grp = QGroupBox("Parámetros de cámara")
        params_grp.setStyleSheet(self._grp_style())
        params_outer = QVBoxLayout(params_grp)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background:{_PANEL};border:none; }}"
            f"QScrollBar:vertical {{ background:{_DARK};width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:{_BORDER};border-radius:4px; }}"
        )
        inner = QWidget()
        inner.setStyleSheet(f"background:{_PANEL};")
        form = QFormLayout(inner)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for pd in _PARAM_DEFS:
            self._add_param_row(form, pd)

        scroll.setWidget(inner)
        params_outer.addWidget(scroll, stretch=1)

        # buttons
        btn_lay = QHBoxLayout()
        save_btn = QPushButton("Guardar configuración")
        save_btn.clicked.connect(self._save_settings)
        save_btn.setStyleSheet(
            f"background:{_ACCENT};color:#000;font-weight:700;"
            "border-radius:6px;padding:7px 18px;"
        )
        def_btn = QPushButton("Restaurar defaults")
        def_btn.clicked.connect(self._restore_defaults)
        def_btn.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:6px;padding:7px 18px;"
        )
        btn_lay.addWidget(save_btn)
        btn_lay.addWidget(def_btn)
        btn_lay.addStretch()
        params_outer.addLayout(btn_lay)

        main.addWidget(params_grp, stretch=2)
        root.addLayout(main, stretch=2)

    def _build_ip_camera_section(self) -> QGroupBox:
        grp = QGroupBox("Cámaras IP")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(18, 24, 18, 18)
        lay.setSpacing(10)

        _field_ss = (
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 9px;font-size:12px;"
        )
        _lbl_ss = f"color:{_MUTED};font-size:11px;"

        # ── Fila 1: selector + URL + botones ─────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self._ip_slot_combo = QComboBox()
        self._ip_slot_combo.addItems(["Cámara IP 1", "Cámara IP 2"])
        self._ip_slot_combo.setFixedWidth(130)
        self._ip_slot_combo.setFixedHeight(34)
        self._ip_slot_combo.setStyleSheet(
            f"QComboBox {{ background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 9px;font-size:12px;font-weight:600; }"
            f"QComboBox::drop-down {{ border:none; }}"
            f"QComboBox QAbstractItemView {{ background:{_PANEL};color:{_TEXT};"
            f"border:1px solid {_BORDER};selection-background-color:{_ACCENT}; }}"
        )
        self._ip_slot_combo.currentIndexChanged.connect(self._on_ip_slot_changed)
        row1.addWidget(self._ip_slot_combo)

        ip_lbl = QLabel("IP de camara")
        ip_lbl.setStyleSheet(_lbl_ss)
        row1.addWidget(ip_lbl)

        self._ip_host_edit = QLineEdit()
        self._ip_host_edit.setFixedHeight(34)
        self._ip_host_edit.setFixedWidth(190)
        self._ip_host_edit.setPlaceholderText("192.168.1.3")
        self._ip_host_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 10px;font-size:12px;"
            f"font-family:Consolas,monospace;selection-background-color:{_ACCENT};"
        )
        self._ip_host_edit.returnPressed.connect(self._on_ip_connect)
        self._ip_host_edit.setPlaceholderText("192.168.1.3")
        self._ip_host_edit.textChanged.connect(lambda _txt: self._sync_ip_generated_url())
        row1.addWidget(self._ip_host_edit)

        self._btn_ip_connect = QPushButton("Conectar")
        self._btn_ip_disconnect = QPushButton("Desconectar")
        self._btn_ip_disconnect.setEnabled(False)
        for _btn, _color in (
            (self._btn_ip_connect,    "#16a34a"),
            (self._btn_ip_disconnect, "#dc2626"),
        ):
            _btn.setFixedHeight(34)
            _btn.setMinimumWidth(100)
            _btn.setStyleSheet(
                f"QPushButton {{ background:{_color};color:#fff;border:none;"
                "border-radius:5px;padding:0 16px;font-size:12px;font-weight:700; }}"
                "QPushButton:hover { opacity: 0.9; }"
                "QPushButton:disabled { background:#334155;color:#64748b; }"
            )
        self._btn_ip_connect.clicked.connect(self._on_ip_connect)
        self._btn_ip_disconnect.clicked.connect(self._on_ip_disconnect)
        row1.addWidget(self._btn_ip_connect)
        row1.addWidget(self._btn_ip_disconnect)

        self._ip_status_lbl = QLabel("-")
        self._ip_status_lbl.setMinimumWidth(170)
        self._ip_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ip_status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:13px;font-weight:600;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:6px;padding:6px 12px;"
        )
        row1.addWidget(self._ip_status_lbl)
        lay.addLayout(row1)

        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        url_lbl = QLabel("URL stream")
        url_lbl.setStyleSheet(_lbl_ss)
        url_row.addWidget(url_lbl)

        self._ip_url_edit = QLineEdit()
        self._ip_url_edit.setFixedHeight(30)
        self._ip_url_edit.setReadOnly(False)
        self._ip_url_edit.setPlaceholderText("URL completa de la camara (se completa automaticamente desde la IP)")
        self._ip_url_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:4px 9px;font-size:11px;"
            "font-family:Consolas,monospace;"
        )
        self._ip_url_edit.returnPressed.connect(self._on_ip_connect)
        url_row.addWidget(self._ip_url_edit, stretch=1)
        lay.addLayout(url_row)

        # ── Fila 2: credenciales ──────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        u_lbl = QLabel("Usuario")
        u_lbl.setStyleSheet(_lbl_ss)
        row2.addWidget(u_lbl)

        self._ip_user_edit = QLineEdit()
        self._ip_user_edit.setFixedHeight(34)
        self._ip_user_edit.setFixedWidth(160)
        self._ip_user_edit.setStyleSheet(_field_ss)
        row2.addWidget(self._ip_user_edit)

        row2.addSpacing(16)
        p_lbl = QLabel("Contraseña")
        p_lbl.setStyleSheet(_lbl_ss)
        row2.addWidget(p_lbl)

        self._ip_pass_edit = QLineEdit()
        self._ip_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._ip_pass_edit.setFixedHeight(34)
        self._ip_pass_edit.setFixedWidth(160)
        self._ip_pass_edit.setStyleSheet(_field_ss)
        row2.addWidget(self._ip_pass_edit)

        self._ip_pass_toggle = QPushButton("Mostrar")
        self._ip_pass_toggle.setFixedHeight(34)
        self._ip_pass_toggle.setCheckable(True)
        self._ip_pass_toggle.setStyleSheet(
            f"QPushButton {{ background:{_DARK};color:{_MUTED};"
            f"border:1px solid {_BORDER};border-radius:5px;"
            "padding:0 10px;font-size:11px; }"
            f"QPushButton:checked {{ color:{_ACCENT};border-color:{_ACCENT}; }}"
        )
        self._ip_pass_toggle.toggled.connect(
            lambda checked: self._ip_pass_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        row2.addWidget(self._ip_pass_toggle)

        row2.addStretch()
        lay.addLayout(row2)
        lay.addSpacing(4)

        # ── Separador ────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_BORDER};background:{_BORDER};max-height:1px;")
        lay.addWidget(sep)

        # ── Guardar URL y credenciales ────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        ip_save_btn = QPushButton("Guardar configuración")
        ip_save_btn.setFixedHeight(32)
        ip_save_btn.clicked.connect(self._save_ip_settings)
        ip_save_btn.setStyleSheet(
            f"QPushButton {{ background:{_ACCENT};color:#000;font-weight:700;"
            "border-radius:5px;padding:0 18px;font-size:12px; }}"
            "QPushButton:hover { background:#67e8f9; }"
        )

        self._ip_save_status = QLabel("")
        self._ip_save_status.setStyleSheet(f"color:{_MUTED};font-size:11px;")

        btn_row.addWidget(ip_save_btn)
        btn_row.addWidget(self._ip_save_status)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # ── Separador ────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{_BORDER};background:{_BORDER};max-height:1px;")
        lay.addWidget(sep2)

        # ── Preview ───────────────────────────────────────────────────
        self._ip_preview = QLabel("Sin señal  —  ingrese la URL y presione Conectar")
        self._ip_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ip_preview.setMinimumHeight(500)
        self._ip_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ip_preview.setStyleSheet(
            f"background:{_DARK};color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:8px;font-size:13px;"
        )
        lay.addWidget(self._ip_preview, stretch=4)

        # ── Barra inferior: Capturar + info FPS ──────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        self._btn_ip_capture = QPushButton("Capturar frame")
        self._btn_ip_capture.setFixedHeight(32)
        self._btn_ip_capture.setEnabled(False)
        self._btn_ip_capture.clicked.connect(self._capture_ip_frame)
        self._btn_ip_capture.setStyleSheet(
            f"QPushButton {{ background:{_PANEL};color:{_TEXT};"
            f"border:1px solid {_BORDER};border-radius:5px;"
            "padding:0 16px;font-size:12px; }"
            f"QPushButton:enabled:hover {{ border-color:{_ACCENT};color:{_ACCENT}; }}"
            "QPushButton:disabled { color:#4b5563; border-color:#2d3748; }"
        )
        bottom_row.addWidget(self._btn_ip_capture)

        self._ip_preview_only_chk = QCheckBox("Solo preview - sin salidas de maquina")
        self._ip_preview_only_chk.setChecked(True)
        self._ip_preview_only_chk.setStyleSheet(f"color:{_WARN};font-size:12px;font-weight:600;")
        bottom_row.addWidget(self._ip_preview_only_chk)

        self._ip_info_lbl = QLabel("-")
        self._ip_info_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:13px;font-weight:600;font-family:Consolas;"
        )
        bottom_row.addWidget(self._ip_info_lbl)
        bottom_row.addStretch()

        self._ip_capture_status = QLabel("")
        self._ip_capture_status.setStyleSheet(f"color:{_OK};font-size:11px;")
        bottom_row.addWidget(self._ip_capture_status)

        lay.addLayout(bottom_row)

        # ── Historial de conexión ─────────────────────────────────────
        self._ip_log = QTextEdit()
        self._ip_log.setReadOnly(True)
        self._ip_log.setFixedHeight(72)
        self._ip_log.setStyleSheet(
            f"QTextEdit {{ background:#070e1c;color:{_MUTED};"
            f"border:1px solid {_BORDER};border-radius:6px;"
            "font-size:10px;font-family:Consolas,monospace;padding:4px 8px; }}"
            f"QScrollBar:vertical {{ background:{_DARK};width:6px; }}"
            f"QScrollBar::handle:vertical {{ background:{_BORDER};border-radius:3px; }}"
        )
        self._ip_log.setPlaceholderText("Historial de conexiones...")
        lay.addWidget(self._ip_log)

        self._load_ip_slot_settings(0)
        return grp

    def _add_param_row(self, form: QFormLayout, pd: _ParamDef) -> None:
        row = QWidget()
        row.setStyleSheet(f"background:{_PANEL};")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)

        # Optional auto checkbox
        if pd.auto_key:
            cb = QCheckBox("Auto")
            cb.setStyleSheet(f"color:{_TEXT};font-size:11px;")
            cb.setChecked(True)
            cb.toggled.connect(lambda checked, ak=pd.auto_key: self._on_auto_toggled(ak, checked))
            self._auto_checks[pd.auto_key] = cb
            hl.addWidget(cb)

        # Slider (mapped 0 -> max-min)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, pd.max_val - pd.min_val)
        slider.setValue(pd.default - pd.min_val)
        slider.setMinimumWidth(160)
        slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height:4px;background:{_BORDER};border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ width:14px;height:14px;margin:-5px 0;"
            f"background:{_ACCENT};border-radius:7px; }}"
            f"QSlider::sub-page:horizontal {{ background:{_ACCENT};border-radius:2px; }}"
        )
        self._sliders[pd.key] = slider

        # SpinBox
        sb = QSpinBox()
        sb.setRange(pd.min_val, pd.max_val)
        sb.setValue(pd.default)
        sb.setFixedWidth(72)
        sb.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 4px;font-size:12px;"
        )
        self._spinboxes[pd.key] = sb

        # Bidirectional link slider ↔ spinbox
        slider.valueChanged.connect(
            lambda v, _pd=pd, _sb=sb: self._on_slider_changed(_pd, v, _sb)
        )
        sb.valueChanged.connect(
            lambda v, _pd=pd, _sl=slider: self._on_spinbox_changed(_pd, v, _sl)
        )

        hl.addWidget(slider, stretch=1)
        hl.addWidget(sb)

        lbl = QLabel(f"<b>{pd.label}</b>")
        lbl.setStyleSheet(f"color:{_TEXT};font-size:12px;")
        form.addRow(lbl, row)

        # Start with auto enabled -> manual controls disabled
        if pd.auto_key:
            slider.setEnabled(False)
            sb.setEnabled(False)

    # ------------------------------------------------------------------
    # Badge de estado IP - helper central
    # ------------------------------------------------------------------

    _STATUS_STYLES = {
        "ok":      ("#22c55e", "#052e16", "#16a34a"),   # text, bg, border
        "warn":    ("#f59e0b", "#1c1507", "#b45309"),
        "error":   ("#f87171", "#1f0606", "#dc2626"),
        "neutral": (_MUTED,   _DARK,    _BORDER),
    }

    def _set_ip_status(self, text: str, kind: str = "neutral") -> None:
        """Actualiza el badge de estado con color semántico y texto completo."""
        fg, bg, border = self._STATUS_STYLES.get(kind, self._STATUS_STYLES["neutral"])
        self._ip_status_lbl.setStyleSheet(
            f"color:{fg};font-size:13px;font-weight:600;"
            f"background:{bg};border:1px solid {border};"
            "border-radius:6px;padding:6px 12px;"
        )
        self._ip_status_lbl.setText(text)

    def _update_ip_status_info(self, slot: int) -> None:
        res = self._ip_last_res[slot]
        fps = self._ip_fps_value[slot]
        fps_str = f"{fps:.0f} fps" if fps > 0 else "..."
        age_str = "sin frame"
        if self._ip_last_frame_t[slot] > 0:
            age_ms = max(0, int((time.monotonic() - self._ip_last_frame_t[slot]) * 1000))
            age_str = f"{age_ms} ms"
        mode = "preview" if self._ip_preview_only_chk.isChecked() else "decision virtual"
        frozen = " | congelada" if self._ip_frozen[slot] else ""
        parts = [
            f"{res} @ {fps_str}" if res else fps_str,
            f"ultimo {age_str}",
            f"drop {self._ip_dropped_frames[slot]}",
            f"recon {self._ip_reconnect_total[slot]}",
            mode,
        ]
        self._ip_info_lbl.setText(" | ".join(parts) + frozen)

    # ------------------------------------------------------------------

    def _build_ip_camera_url(self, value: str, template_url: str = "") -> str:
        raw = value.strip()
        if not raw:
            return ""
        if raw.isdigit():
            return raw
        lower = raw.lower()
        if lower.startswith(("http://", "https://", "rtsp://")):
            return raw
        host = raw.split("/", 1)[0].strip()
        if not host:
            return ""
        if template_url:
            try:
                from urllib.parse import urlparse, urlunparse

                parsed = urlparse(template_url)
                if parsed.scheme and parsed.netloc:
                    port = f":{parsed.port}" if parsed.port and ":" not in host else ""
                    return urlunparse(parsed._replace(netloc=f"{host}{port}"))
            except Exception:
                pass
        return f"http://{host}/oneshotimage.jpg"

    def _extract_ip_camera_host(self, settings: dict, default_host: str = "") -> str:
        host = str(settings.get("ip_address", "")).strip()
        if host:
            return host
        url = str(settings.get("url", "")).strip()
        if not url:
            return default_host
        if url.isdigit():
            return url
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.hostname or default_host
        except Exception:
            return default_host

    def _sync_ip_generated_url(self) -> str:
        if not hasattr(self, "_ip_url_edit"):
            return ""
        current = self._ip_url_edit.text().strip()
        previous_auto = self._ip_auto_urls[self._ip_slot]
        auto_url = self._build_ip_camera_url(
            self._ip_host_edit.text(),
            previous_auto or current,
        )
        if not current or current == previous_auto:
            self._ip_url_edit.setText(auto_url)
            current = auto_url
        self._ip_auto_urls[self._ip_slot] = auto_url
        return current

    def _ip_param_base_url(self) -> str:
        from urllib.parse import urlparse

        url = self._sync_ip_generated_url()
        if not url.lower().startswith(("http://", "https://")):
            return ""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _ip_basic_auth_header(self) -> dict[str, str]:
        import base64

        user = self._ip_user_edit.text().strip()
        password = self._ip_pass_edit.text().strip()
        if not user or not password:
            return {}
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _ip_param_request(self, query: str) -> tuple[bool, str]:
        from urllib.request import Request, urlopen

        base = self._ip_param_base_url()
        if not base:
            return False, "Sin URL configurada"

        headers = self._ip_basic_auth_header()
        endpoints = [
            f"{base}/param.cgi?{query}",
            f"{base}/cgi-bin/param.cgi?{query}",
        ]
        last_error = "Sin respuesta"
        for endpoint in endpoints:
            try:
                req = Request(endpoint, headers=headers)
                with urlopen(req, timeout=5) as resp:
                    body = resp.read().decode("utf-8", errors="replace").strip()
                if body and not body.startswith("# Error"):
                    return True, body
                last_error = body or "Respuesta vacia"
            except Exception as exc:
                last_error = str(exc)
        return False, last_error

    def _read_ip_camera_params(self) -> tuple[bool, dict[str, int] | str]:
        pairs: dict[str, int] = {}
        query = "action=list&group=ImageSource.I0.Sensor"
        ok, body = self._ip_param_request(query)
        if not ok:
            return False, body
        for line in str(body).splitlines():
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            key = name.strip().split(".")[-1]
            for local_key, vapix_key in _IP_VAPIX_MAP.items():
                if vapix_key.endswith(f".{key}"):
                    try:
                        pairs[local_key] = int(float(value.strip()))
                    except ValueError:
                        pass
        return True, pairs

    def _on_ip_slot_changed(self, index: int) -> None:
        self._ip_slot = index
        self._load_ip_slot_settings(index)
        connected = (self._ip_workers[index] is not None or
                     self._ip_caps[index] is not None)
        self._btn_ip_connect.setEnabled(not connected)
        self._btn_ip_disconnect.setEnabled(connected)
        self._ip_host_edit.setEnabled(not connected)
        self._ip_url_edit.setEnabled(not connected)
        self._ip_user_edit.setEnabled(not connected)
        self._ip_pass_edit.setEnabled(not connected)
        if connected:
            res = self._ip_last_res[index]
            fps = self._ip_fps_value[index]
            if fps > 0:
                self._set_ip_status("En vivo", "ok")
            else:
                self._set_ip_status("Conectando...", "warn")
            self._ip_info_lbl.setText(
                f"{res}  @  {fps:.0f} fps" if res and fps > 0 else "-"
            )
            self._btn_ip_capture.setEnabled(self._ip_last_frames[index] is not None)
        else:
            self._set_ip_status("-")
            self._ip_info_lbl.setText("-")
            self._btn_ip_capture.setEnabled(False)
            if hasattr(self, "_ip_preview"):
                self._ip_preview.setPixmap(QPixmap())
                self._ip_preview.setText("Sin señal")

    def _load_ip_slot_settings(self, slot: int) -> None:
        """Carga URL, credenciales y parámetros desde camera.yaml para el slot dado."""
        import yaml
        key = f"ip_camera_{slot + 1}"
        settings: dict = {}
        try:
            p = Path("config/camera.yaml")
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                settings = data.get(key, {})
        except Exception:
            pass
        default_host = "192.168.1.3" if slot == 0 else "192.168.1.2"
        host = self._extract_ip_camera_host(settings, default_host)
        self._ip_host_edit.setText(host)
        url = str(settings.get("url", "")).strip()
        if url:
            self._ip_url_edit.setText(url)
            self._ip_auto_urls[slot] = url
        else:
            generated = self._build_ip_camera_url(host)
            self._ip_url_edit.setText(generated)
            self._ip_auto_urls[slot] = generated
        self._ip_user_edit.setText(str(settings.get("username", "")))
        self._ip_pass_edit.setText(str(settings.get("password", "")))

    # ------------------------------------------------------------------
    # Conexión IP - lógica interna
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Conexión manual: NO se auto-conecta al abrir el tab. El auto-connect
        # disparaba polling WiFi en background apenas se mostraba la pestaña, lo
        # que saturaba red/CPU y ralentizaba la UI cuando la cámara no respondía.
        # Ahora el operador debe presionar "Conectar" para iniciar la conexión.

    def _auto_connect_if_saved(self) -> None:
        """Conecta automáticamente los slots con URL guardada que no fueron
        desconectados manualmente por el operador."""
        import yaml
        p = Path("config/camera.yaml")
        if not p.exists():
            return
        try:
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return
        for slot in range(2):
            if self._ip_manual_disc[slot]:
                continue
            if self._ip_workers[slot] is not None or self._ip_caps[slot] is not None:
                continue
            cfg = data.get(f"ip_camera_{slot + 1}", {})
            url = str(cfg.get("url", "")).strip()
            if not url:
                url = self._build_ip_camera_url(str(cfg.get("ip_address", "")).strip())
            if not url:
                continue
            user     = cfg.get("username") or None
            password = cfg.get("password") or None
            self._start_ip_connection(slot, url, user, password)
            if slot == self._ip_slot:
                self._set_ip_status("Conectando...", "warn")
                self._btn_ip_connect.setEnabled(False)
                self._btn_ip_disconnect.setEnabled(True)
                self._ip_host_edit.setEnabled(False)
                self._ip_url_edit.setEnabled(False)
                self._ip_user_edit.setEnabled(False)
                self._ip_pass_edit.setEnabled(False)
                self._ip_preview.setText("")

    def _start_ip_connection(self, slot: int, url: str,
                             user: "str | None", password: "str | None") -> None:
        """Inicia la conexión para un slot sin modificar la UI."""
        self._disconnect_ip_slot(slot)
        self._ip_retry_counts[slot] = 0
        self._ip_prev_sig[slot] = None
        self._ip_frozen_since[slot] = 0.0
        self._ip_frozen[slot] = False
        source = int(url) if url.isdigit() else url
        if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
            source_l = source.lower()
            if any(tok in source_l for tok in (".jpg", ".jpeg", "oneshot", "snapshot")):
                worker = _HTTPSnapshotReader(source, self, username=user, password=password)
            else:
                worker = _MJPEGReader(source, self, username=user, password=password)
            worker.frame_ready_meta.connect(
                lambda f, m, _s=slot: self._on_ip_frame_ready(f, _s, m)
            )
            worker.error_occurred.connect(lambda m, _s=slot: self._on_ip_error(m, _s))
            self._ip_workers[slot] = worker
            worker.start()
        else:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                cap.release()
                return
            self._ip_caps[slot] = cap
            self._ip_timers[slot].start(200)

    def _log_ip_event(self, slot: int, msg: str, kind: str = "info") -> None:
        """Agrega una línea al historial de conexión con timestamp."""
        if not hasattr(self, "_ip_log"):
            return
        colors = {"ok": _OK, "warn": _WARN, "error": _NOK, "info": _MUTED}
        color  = colors.get(kind, _MUTED)
        ts     = datetime.now().strftime("%H:%M:%S")
        prefix = {"ok": "✓", "warn": "◑", "error": "✗", "info": "·"}[kind]
        line   = (
            f'<span style="color:#475569">[{ts}]</span> '
            f'<span style="color:#64748b">Cám {slot+1}</span> '
            f'<span style="color:{color}">{prefix} {msg}</span>'
        )
        self._ip_log.append(line)
        # Mantener máximo 80 líneas
        doc = self._ip_log.document()
        if doc.blockCount() > 80:
            cursor = self._ip_log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _on_ip_connect(self) -> None:
        slot = self._ip_slot
        url = self._ip_url_edit.text().strip()
        if not url:
            url = self._sync_ip_generated_url()
        if not url:
            self._set_ip_status("Ingrese una IP", "warn")
            return
        self._ip_manual_disc[slot] = False
        self._set_ip_status("Conectando...", "warn")
        self._log_ip_event(slot, f"Conectando a {url[:50]}", "info")
        user     = self._ip_user_edit.text().strip() or None
        password = self._ip_pass_edit.text().strip() or None
        self._start_ip_connection(slot, url, user, password)
        self._btn_ip_connect.setEnabled(False)
        self._btn_ip_disconnect.setEnabled(True)
        self._ip_host_edit.setEnabled(False)
        self._ip_url_edit.setEnabled(False)
        self._ip_user_edit.setEnabled(False)
        self._ip_pass_edit.setEnabled(False)
        self._ip_preview.setText("")

    def _on_ip_disconnect(self) -> None:
        slot = self._ip_slot
        self._ip_manual_disc[slot] = True
        self._log_ip_event(slot, "Desconectado por el operador", "warn")
        self._disconnect_ip_slot(slot)
        if hasattr(self, "_ip_preview"):
            self._ip_preview.setPixmap(QPixmap())
            self._ip_preview.setText("Sin señal")
        self._btn_ip_connect.setEnabled(True)
        self._btn_ip_disconnect.setEnabled(False)
        self._ip_host_edit.setEnabled(True)
        self._ip_url_edit.setEnabled(True)
        self._ip_user_edit.setEnabled(True)
        self._ip_pass_edit.setEnabled(True)
        self._set_ip_status("-")
        self._btn_ip_capture.setEnabled(False)
        self._ip_info_lbl.setText("-")

    def _disconnect_ip_slot(self, slot: int) -> None:
        self._ip_retry_timers[slot].stop()
        self._ip_timers[slot].stop()
        if self._ip_workers[slot] is not None:
            self._ip_workers[slot].stop()
            self._ip_workers[slot] = None
        if self._ip_caps[slot] is not None:
            self._ip_caps[slot].release()
            self._ip_caps[slot] = None

    def _on_ip_error(self, msg: str, slot: int) -> None:
        self._disconnect_ip_slot(slot)
        if self._ip_manual_disc[slot]:
            return
        # Sin reintento automático: si la cámara WiFi queda inalcanzable, un bucle
        # de reconexión satura red/CPU y deja la UI lenta. Mostramos el error y
        # dejamos que el operador reintente manualmente con "Conectar".
        self._save_ip_diagnostic_snapshot(slot, f"error_{msg}")
        self._log_ip_event(slot, f"Error: {msg[:60]} — presione Conectar para reintentar", "error")
        if slot == self._ip_slot:
            self._set_ip_status("Sin conexion", "error")
            self._btn_ip_connect.setEnabled(True)
            self._btn_ip_disconnect.setEnabled(False)
            self._btn_ip_capture.setEnabled(False)
            self._ip_host_edit.setEnabled(True)
            self._ip_url_edit.setEnabled(True)
            self._ip_user_edit.setEnabled(True)
            self._ip_pass_edit.setEnabled(True)

    def _on_ip_retry(self, slot: int) -> None:
        if self._ip_manual_disc[slot]:
            return
        if self._ip_workers[slot] is not None or self._ip_caps[slot] is not None:
            return
        import yaml
        p = Path("config/camera.yaml")
        if not p.exists():
            return
        try:
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return
        cfg  = data.get(f"ip_camera_{slot + 1}", {})
        url  = str(cfg.get("url", "")).strip()
        if not url:
            url = self._build_ip_camera_url(str(cfg.get("ip_address", "")).strip())
        if not url:
            return
        user     = cfg.get("username") or None
        password = cfg.get("password") or None
        self._ip_reconnect_total[slot] += 1
        self._log_ip_event(slot, f"Reconectando intento {self._ip_retry_counts[slot]}...", "warn")
        self._start_ip_connection(slot, url, user, password)
        if slot == self._ip_slot:
            n = self._ip_retry_counts[slot]
            self._set_ip_status(f"Intentando conectar... ({n})", "warn")

    def _ip_frame_signature(self, frame):
        h, w = frame.shape[:2]
        y1, y2 = h // 4, max(h // 4 + 1, (h * 3) // 4)
        x1, x2 = w // 4, max(w // 4 + 1, (w * 3) // 4)
        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA)
        return small.astype(np.int16)

    def _update_ip_freeze_watchdog(self, frame, slot: int, now: float) -> bool:
        sig = self._ip_frame_signature(frame)
        prev = self._ip_prev_sig[slot]
        self._ip_prev_sig[slot] = sig
        if prev is None:
            self._ip_frozen_since[slot] = 0.0
            self._ip_frozen[slot] = False
            return False
        diff = float(np.mean(np.abs(sig - prev)))
        if diff < 0.20:
            if self._ip_frozen_since[slot] <= 0:
                self._ip_frozen_since[slot] = now
            elif now - self._ip_frozen_since[slot] >= 6.0:
                if not self._ip_frozen[slot]:
                    self._save_ip_diagnostic_snapshot(slot, "senal_congelada")
                self._ip_frozen[slot] = True
        else:
            self._ip_frozen_since[slot] = 0.0
            self._ip_frozen[slot] = False
        return self._ip_frozen[slot]

    def _save_ip_diagnostic_snapshot(self, slot: int, reason: str) -> None:
        frame = self._ip_last_frames[slot]
        if frame is None:
            return
        now = time.monotonic()
        if now - self._ip_last_diag_snapshot_t[slot] < 10.0:
            return
        self._ip_last_diag_snapshot_t[slot] = now
        out_dir = Path("data/output/export")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        safe_reason = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in reason)
        out_path = out_dir / f"diagnostico_ip{slot + 1}_{safe_reason}_{ts}.jpg"
        try:
            cv2.imwrite(str(out_path), frame)
            if slot == self._ip_slot:
                self._ip_capture_status.setStyleSheet(f"color:{_WARN};font-size:11px;")
                self._ip_capture_status.setText(f"Diag: {out_path.name}")
                QTimer.singleShot(5000, lambda: self._ip_capture_status.setText(""))
        except Exception:
            logger.exception("No se pudo guardar snapshot diagnostico IP")

    def _on_ip_frame_ready(self, frame, slot: int, meta: Optional[dict] = None) -> None:
        self._ip_last_frames[slot] = frame
        now = time.monotonic()
        self._ip_last_frame_t[slot] = now
        if meta:
            self._ip_dropped_frames[slot] += int(meta.get("dropped", 0) or 0)
        self._ip_fps_count[slot] += 1
        if self._ip_fps_last_t[slot] == 0.0:
            self._ip_fps_last_t[slot] = now
        elapsed = now - self._ip_fps_last_t[slot]
        if self._ip_fps_count[slot] >= 20 and elapsed > 0:
            self._ip_fps_value[slot] = self._ip_fps_count[slot] / elapsed
            self._ip_fps_count[slot] = 0
            self._ip_fps_last_t[slot] = now
        fh, fw = frame.shape[:2]
        self._ip_last_res[slot] = f"{fw}x{fh}"
        # Log solo en el primer frame recibido tras conectar
        if self._ip_fps_count[slot] == 1 and self._ip_retry_counts[slot] == 0:
            self._log_ip_event(slot, f"Señal recibida  {fw}x{fh}", "ok")
        if slot == self._ip_slot:
            frozen = self._update_ip_freeze_watchdog(frame, slot, now)
            fps_str = (f"{self._ip_fps_value[slot]:.0f} fps"
                       if self._ip_fps_value[slot] > 0 else "...")
            if frozen:
                self._set_ip_status("SENAL CONGELADA", "error")
            else:
                self._set_ip_status("En vivo", "ok")
            self._update_ip_status_info(slot)
            self._btn_ip_capture.setEnabled(True)
            self._show_ip_frame(frame)

    def _refresh_ip_camera(self, slot: int) -> None:
        cap = self._ip_caps[slot]
        if cap is None or not cap.isOpened():
            self._disconnect_ip_slot(slot)
            if slot == self._ip_slot:
                self._on_ip_error("señal perdida", slot)
            return
        ret, frame = cap.read()
        if not ret:
            return
        self._on_ip_frame_ready(frame, slot, {"dropped": 0})

    def _show_ip_frame(self, frame) -> None:
        rect = self._ip_preview.contentsRect()
        w = max(640, rect.width() - 4)
        h = max(420, rect.height() - 4)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        fh, fw = rgb.shape[:2]
        qi = QImage(rgb.data, fw, fh, fw * 3, QImage.Format.Format_RGB888)
        pxm = QPixmap.fromImage(qi).scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._ip_preview.setPixmap(pxm)

    def _capture_ip_frame(self) -> None:
        """Guarda el frame actual del slot visible en data/output/export/."""
        frame = self._ip_last_frames[self._ip_slot]
        if frame is None:
            return
        out_dir = Path("data/output/export")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        slot_name = f"ip{self._ip_slot + 1}"
        out_path = out_dir / f"captura_{slot_name}_{ts}.jpg"
        try:
            cv2.imwrite(str(out_path), frame)
            self._ip_capture_status.setStyleSheet(f"color:{_OK};font-size:11px;")
            self._ip_capture_status.setText(f"Guardado: {out_path.name}")
            # Limpiar el mensaje después de 4 segundos
            QTimer.singleShot(4000, lambda: self._ip_capture_status.setText(""))
        except Exception as exc:
            self._ip_capture_status.setStyleSheet(f"color:{_NOK};font-size:11px;")
            self._ip_capture_status.setText(f"Error: {exc}")

    def _save_ip_settings(self) -> None:
        """Guarda IP, URL y credenciales en camera.yaml y conecta inmediatamente."""
        slot = self._ip_slot
        key  = f"ip_camera_{slot + 1}"
        url  = self._sync_ip_generated_url()
        if not url:
            self._ip_save_status.setStyleSheet(f"color:{_NOK};font-size:11px;")
            self._ip_save_status.setText("Ingresá la IP primero")
            return
        settings: dict = {
            "ip_address": self._ip_host_edit.text().strip(),
            "url":        url,
            "username":   self._ip_user_edit.text().strip(),
            "password":   self._ip_pass_edit.text().strip(),
        }
        try:
            camera_config.save_camera_settings(key, settings)
        except Exception as exc:
            self._ip_save_status.setStyleSheet(f"color:{_NOK};font-size:11px;")
            self._ip_save_status.setText(f"Error al guardar: {exc}")
            return

        # Permitir auto-connect (cancela cualquier desconexión manual previa)
        self._ip_manual_disc[slot] = False

        # Conectar ahora si no está ya conectado
        if self._ip_workers[slot] is None and self._ip_caps[slot] is None:
            user     = settings["username"] or None
            password = settings["password"] or None
            self._start_ip_connection(slot, url, user, password)
            if slot == self._ip_slot:
                self._set_ip_status("Conectando...", "warn")
                self._btn_ip_connect.setEnabled(False)
                self._btn_ip_disconnect.setEnabled(True)
                self._ip_url_edit.setEnabled(False)
                self._ip_user_edit.setEnabled(False)
                self._ip_pass_edit.setEnabled(False)
                self._ip_preview.setText("")

        self._ip_save_status.setStyleSheet(f"color:{_OK};font-size:11px;")
        self._ip_save_status.setText("Guardado — se conectará al iniciar")
        QTimer.singleShot(3000, lambda: self._ip_save_status.setText(""))

    def _auto_connect_all_slots(self) -> None:
        """Conecta ambas cámaras IP al abrir la pestaña usando los ajustes guardados."""
        import yaml as _yaml
        try:
            p = Path("config/camera.yaml")
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            data = {}

        for slot in range(2):
            if self._ip_manual_disc[slot]:
                continue
            if self._ip_workers[slot] is not None or self._ip_caps[slot] is not None:
                continue  # ya conectado
            cfg  = data.get(f"ip_camera_{slot + 1}", {})
            url  = str(cfg.get("url", "")).strip()
            if not url:
                ip = str(cfg.get("ip_address", "")).strip()
                if ip:
                    url = self._build_ip_camera_url(ip)
            if not url:
                continue
            user = str(cfg.get("username", "")).strip() or None
            pwd  = str(cfg.get("password", "")).strip() or None
            self._start_ip_connection(slot, url, user, pwd)

        # Actualizar UI para el slot visible
        slot = self._ip_slot
        if self._ip_workers[slot] is not None or self._ip_caps[slot] is not None:
            self._set_ip_status("Conectando...", "warn")
            self._btn_ip_connect.setEnabled(False)
            self._btn_ip_disconnect.setEnabled(True)
            self._ip_url_edit.setEnabled(False)
            self._ip_user_edit.setEnabled(False)
            self._ip_pass_edit.setEnabled(False)
            self._ip_preview.setText("")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_scanner_changed(self, scanner_id: str) -> None:
        running = self._get_camera_running(scanner_id)
        self._cam_btn.setText("Detener cámara" if running else "Iniciar cámara")
        if running:
            self._preview_timer.start()
        else:
            self._preview_timer.stop()
            self._preview_lbl.setText("Sin señal de cámara")

    def _toggle_camera(self) -> None:
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        cam = self._system.camera(scanner_id)
        if cam.is_running:
            cam.stop()
            self._preview_timer.stop()
            self._cam_btn.setText("Iniciar cámara")
            self._preview_lbl.setText("Sin señal de cámara")
        else:
            ok = cam.start()
            if ok:
                self._preview_timer.start()
                self._cam_btn.setText("Detener cámara")
                self._status_lbl.setText("Cámara iniciada")
            else:
                self._status_lbl.setStyleSheet(f"color:{_NOK};font-size:11px;")
                self._status_lbl.setText("Error al abrir cámara")

    def _on_slider_changed(self, pd: _ParamDef, slider_val: int, sb: QSpinBox) -> None:
        real_val = pd.min_val + slider_val
        sb.blockSignals(True)
        sb.setValue(real_val)
        sb.blockSignals(False)
        if not self._block_apply:
            self._apply_param(pd.key, real_val)

    def _on_spinbox_changed(self, pd: _ParamDef, val: int, slider: QSlider) -> None:
        slider.blockSignals(True)
        slider.setValue(val - pd.min_val)
        slider.blockSignals(False)
        if not self._block_apply:
            self._apply_param(pd.key, val)

    def _on_auto_toggled(self, auto_key: str, checked: bool) -> None:
        # Find the param that owns this auto_key and enable/disable its controls
        for pd in _PARAM_DEFS:
            if pd.auto_key == auto_key:
                self._sliders[pd.key].setEnabled(not checked)
                self._spinboxes[pd.key].setEnabled(not checked)
                break
        if not self._block_apply:
            self._apply_param(auto_key, 1.0 if checked else 0.0)

    def _apply_param(self, key: str, value: float) -> None:
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        try:
            cam = self._system.camera(scanner_id)
            if cam.is_running:
                ok = cam.apply_setting(key, value)
                dot = "ON" if ok else "OFF"
                self._status_lbl.setStyleSheet(
                    f"color:{'#4ade80' if ok else '#fbbf24'};font-size:11px;"
                )
                self._status_lbl.setText(f"{dot} {key}={value:.0f}")
        except KeyError:
            pass

    def _update_preview(self) -> None:
        if not self.isVisible():
            return
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        try:
            cam = self._system.camera(scanner_id)
        except KeyError:
            return
        if not cam.is_running:
            return
        frame = cam.get_frame()
        if frame is None:
            return
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img)
        target = self._preview_lbl.size()
        self._preview_lbl.setPixmap(
            pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.FastTransformation)
        )

    def _read_from_camera(self) -> None:
        """Read all current property values from the camera driver and update UI."""
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        try:
            cam = self._system.camera(scanner_id)
        except KeyError:
            return
        if not cam.is_running:
            self._status_lbl.setText("Cámara no iniciada")
            return

        self._block_apply = True
        try:
            for pd in _PARAM_DEFS:
                val = cam.read_setting(pd.key)
                if val >= 0:
                    sb = self._spinboxes[pd.key]
                    sb.setValue(int(round(val)))
            for auto_key, cb in self._auto_checks.items():
                val = cam.read_setting(auto_key)
                if val >= 0:
                    cb.setChecked(bool(val > 0.5))
        finally:
            self._block_apply = False

        self._status_lbl.setStyleSheet(f"color:{_OK};font-size:11px;")
        self._status_lbl.setText("Valores leídos de la cámara")

    def _save_settings(self) -> None:
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        settings: dict = {}
        for pd in _PARAM_DEFS:
            settings[pd.key] = self._spinboxes[pd.key].value()
        for auto_key, cb in self._auto_checks.items():
            settings[auto_key] = cb.isChecked()
        try:
            camera_config.save_camera_settings(scanner_id, settings)
            self._status_lbl.setStyleSheet(f"color:{_OK};font-size:11px;")
            self._status_lbl.setText("Guardado en config/camera.yaml")
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo guardar: {exc}")

    def _restore_defaults(self) -> None:
        self._block_apply = True
        try:
            for pd in _PARAM_DEFS:
                self._spinboxes[pd.key].setValue(pd.default)
                if pd.auto_key and pd.auto_key in self._auto_checks:
                    self._auto_checks[pd.auto_key].setChecked(True)
        finally:
            self._block_apply = False
        # Apply all defaults at once
        for pd in _PARAM_DEFS:
            self._apply_param(pd.key, float(pd.default))
        for auto_key, cb in self._auto_checks.items():
            self._apply_param(auto_key, 1.0 if cb.isChecked() else 0.0)
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        self._status_lbl.setText("Defaults restaurados (no guardados)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_scanner_selector(self) -> None:
        ids = self._system.scanner_ids()
        self._scanner_combo.addItems(ids)
        if ids:
            self._on_scanner_changed(ids[0])

    def _get_camera_running(self, scanner_id: str) -> bool:
        try:
            return self._system.camera(scanner_id).is_running
        except KeyError:
            return False

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )


# ==================================================================
# Tab: Simulación de FSM de scanner
# ==================================================================

class ScannerSimTab(QWidget):
    """
    Simulación de ciclo completo AUTO sin cámara real.

    Permite probar el recorrido:
      IDLE -> [Iniciar] -> RUNNING verde
           -> [Inyectar OK/NOK] -> luces amarilla/verde
           -> [1/3 NOK] -> streak parcial
           -> [Forzar FAULT] -> FAULT rojo parpadeante
           -> [Detener] -> STOPPED
           -> [Reset] -> IDLE azul
    """

    _STATE_COLORS = {
        ScannerState.IDLE:    ("#1e3a5f", "#93c5fd"),
        ScannerState.RUNNING: ("#14532d", "#86efac"),
        ScannerState.FAULT:   ("#7f1d1d", "#fca5a5"),
        ScannerState.STOPPED: ("#1e293b", "#94a3b8"),
        ScannerState.ERROR:   ("#78350f", "#fcd34d"),
    }
    _STATE_LABELS = {
        ScannerState.IDLE:    "IDLE",
        ScannerState.RUNNING: "RUNNING",
        ScannerState.FAULT:   "FAULT",
        ScannerState.STOPPED: "PARADO",
        ScannerState.ERROR:   "ERROR",
    }

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._state_lbls: dict[str, QLabel] = {}
        self._streak_lbls: dict[str, QLabel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{_DARK}; }}")

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        title = QLabel("Simulación de ciclo de producción - sin cámara real")
        title.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        desc = QLabel(
            "Inicia el scanner en modo AUTO (solenoide + backlight ON, luces PLC activas) "
            "sin requerir cámara. Usa los botones para simular resultados de inspección "
            "y verificar la FSM completa: IDLE -> RUNNING -> FAULT -> STOPPED -> IDLE."
        )
        desc.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        for sid in self._system.scanner_ids():
            scanner = self._system.scanner(sid)
            cfg     = self._system.io.scanner_config(sid)
            from src.utils.config import load_tolerances
            tols      = load_tolerances()
            insp_cfg  = cfg.get("inspection", {})
            threshold = int(insp_cfg.get(
                "consecutive_nok_frames",
                tols.get("consecutive_nok_frames", 5)
            ))
            nok_third = max(1, threshold // 3)

            grp = QGroupBox(sid.replace("_", " ").upper())
            grp.setStyleSheet(self._grp_style())
            grp_lay = QVBoxLayout(grp)
            grp_lay.setContentsMargins(14, 16, 14, 14)
            grp_lay.setSpacing(12)

            # ── Estado actual ───────────────────────────────────────
            info_row = QHBoxLayout()
            info_row.setSpacing(16)

            state_lbl = QLabel("IDLE")
            state_lbl.setFixedSize(110, 34)
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            state_lbl.setStyleSheet(
                f"background:#1e3a5f;color:#93c5fd;border-radius:6px;"
                "font-size:13px;font-weight:700;"
            )
            self._state_lbls[sid] = state_lbl
            info_row.addWidget(state_lbl)

            streak_lbl = QLabel("Racha NOK: 0")
            streak_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
            self._streak_lbls[sid] = streak_lbl
            info_row.addWidget(streak_lbl)

            thr_lbl = QLabel(f"Umbral FAULT: {threshold}  -  1/3 = {nok_third}")
            thr_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
            info_row.addWidget(thr_lbl)
            info_row.addStretch()
            grp_lay.addLayout(info_row)

            # ── Botones de acción ────────────────────────────────────
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)

            def _btn(text: str, color: str, w: int = 120) -> QPushButton:
                b = QPushButton(text)
                b.setFixedSize(w, 44)
                b.setStyleSheet(
                    f"background:{color};color:white;border-radius:7px;"
                    "font-size:11px;font-weight:700;border:none;"
                )
                return b

            b_start  = _btn("Iniciar\n(sim AUTO)", "#166534")
            b_ok     = _btn("OK\nInyectar",        "#1e40af", 110)
            b_nok1   = _btn("NOK\n(x1)",           "#7f1d1d", 110)
            b_nok3   = _btn(f"NOK x{nok_third}\n(1/3 umbral)", "#92400e", 120)
            b_fault  = _btn("Forzar FAULT",        "#831843", 120)
            b_stop   = _btn("Detener",              "#374151", 100)
            b_reset  = _btn("Reset",                "#1e3a5f", 90)

            b_start.clicked.connect(lambda _, s=sid: self._system.scanner(s).start_simulate())
            b_ok.clicked.connect(   lambda _, s=sid: self._system.scanner(s).inject_result(True))
            b_nok1.clicked.connect( lambda _, s=sid: self._system.scanner(s).inject_result(False, 1))
            b_nok3.clicked.connect( lambda _, s=sid, n=nok_third:
                                        self._system.scanner(s).inject_result(False, n))
            b_fault.clicked.connect(lambda _, s=sid:
                                        self._system.scanner(s).force_fault())
            b_stop.clicked.connect( lambda _, s=sid: self._system.scanner(s).stop())
            b_reset.clicked.connect(lambda _, s=sid: self._system.scanner(s).reset())

            for b in (b_start, b_ok, b_nok1, b_nok3, b_fault, b_stop, b_reset):
                btn_row.addWidget(b)
            btn_row.addStretch()
            grp_lay.addLayout(btn_row)

            # ── Secuencia sugerida ───────────────────────────────────
            seq = QLabel(
                "Secuencia: Iniciar -> Inyectar OK -> 1/3 NOK -> Forzar FAULT -> Detener -> Reset"
            )
            seq.setStyleSheet(f"color:#475569;font-size:10px;font-style:italic;")
            grp_lay.addWidget(seq)

            lay.addWidget(grp)

        lay.addStretch()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def refresh(self) -> None:
        for sid, lbl in self._state_lbls.items():
            s = self._system.scanner(sid).get_status()
            state: ScannerState = s["state"]
            bg, fg = self._STATE_COLORS.get(state, ("#1e293b", "#94a3b8"))
            lbl.setText(self._STATE_LABELS.get(state, state.value.upper()))
            lbl.setStyleSheet(
                f"background:{bg};color:{fg};border-radius:6px;"
                "font-size:13px;font-weight:700;"
            )
            streak_lbl = self._streak_lbls.get(sid)
            if streak_lbl:
                streak_lbl.setText(f"Racha NOK: {s['nok_streak']}")

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _logo_label(rel_path: str, max_h: int) -> QLabel:
    """Carga logo desde raíz del proyecto escalado a max_h px de alto."""
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


# ==================================================================
# Ventana de servicio
# ==================================================================

class ServiceWindow(QMainWindow):
    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self.setWindowTitle("DEFYVISION - Modo Servicio")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.resize(1200, 760)

        self._log_handler = QtLogHandler()
        logging.getLogger().addHandler(self._log_handler)
        self._last_plc_connected: bool | None = None

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background:{_DARK};")
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background:{_PANEL};border:1px solid {_BORDER};border-radius:8px;
            }}
            QTabBar::tab {{
                background:{_DARK};color:{_MUTED};
                padding:8px 20px;font-size:12px;border-radius:5px;margin-right:3px;
            }}
            QTabBar::tab:selected {{
                background:{_PANEL};color:{_TEXT};font-weight:700;
            }}
        """)

        self._plc_tab    = PLCIOTab(self._system)
        self._diag_tab   = PLCDiagTab(self._system)
        self._sys_tab    = SystemTab(self._system)
        self._log_tab    = LogsTab(self._log_handler)
        self._cfg_tab    = ConfigTab()
        self._rec_tab    = RecordingTab(self._system)
        self._events_tab = EventBrowserTab(self._system)
        self._cam_tab    = CameraCalibTab(self._system)

        # ── Tab "Cámara": sub-tabs GRABACIÓN / ANÁLISIS / CALIBRACIÓN ─
        _sub_style = (
            f"QTabWidget::pane {{ border:none; background:{_DARK}; }}"
            f"QTabBar::tab {{ background:{_PANEL};color:{_MUTED};"
            "padding:10px 24px;font-size:13px;font-weight:700;letter-spacing:1px; }"
            f"QTabBar::tab:selected {{ background:{_DARK};color:{_TEXT};"
            f"border-bottom:3px solid {_ACCENT}; }}"
            f"QTabBar::tab:hover {{ color:{_TEXT}; }}"
        )
        cam_tabs = QTabWidget()
        cam_tabs.setStyleSheet(_sub_style)
        cam_tabs.addTab(self._rec_tab._grab_page, "  GRABACIÓN  ")
        cam_tabs.addTab(self._rec_tab._ana_page,  "  ANÁLISIS  ")
        cam_tabs.addTab(self._rec_tab._cal_page,  "  CALIBRACIÓN  ")
        cam_tabs.addTab(self._cam_tab,             "  CONEXIÓN  ")

        self._tabs.addTab(self._plc_tab,    "  PLC I/O  ")
        self._tabs.addTab(self._diag_tab,   "  Diagnóstico  ")
        self._tabs.addTab(self._sys_tab,    "  Sistema  ")
        self._tabs.addTab(self._log_tab,    "  Logs  ")
        self._tabs.addTab(self._cfg_tab,    "  Config  ")
        self._tabs.addTab(self._events_tab, "  Evidencias  ")
        self._tabs.addTab(cam_tabs,         "  Cámara  ")

        # Health bar — referencia al cam_tab se pasa por lista mutable
        self._cam_ref: list = [self._cam_tab]
        self._health_bar = _HealthBar(self._system, self._cam_ref, central)
        root.addWidget(self._health_bar)
        root.addWidget(self._tabs, stretch=1)
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _build_header(self) -> QWidget:
        """
        Header oscuro con logos reales - misma estructura que OperatorWindow.

        Layout de 3 secciones de ancho fijo igual (_HEADER_WING_W px c/u):
          [ala izquierda] | [centro: título, stretch=1] | [ala derecha]
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
        subtitle = QLabel("Modo Servicio  ·  Diagnóstico")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color:#475569;font-size:10px;letter-spacing:1.5px;"
            "background:transparent;"
        )
        center_lay.addWidget(title)
        center_lay.addWidget(subtitle)
        outer.addWidget(center, stretch=1)

        # ── Ala derecha: PLC badge + logo DEFYMOTION ─────────────────
        right_wing = QWidget()
        right_wing.setFixedWidth(_HEADER_WING_W)
        right_wing.setStyleSheet("background:transparent;")
        right_lay = QHBoxLayout(right_wing)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(10)

        ctrl = QWidget()
        ctrl.setStyleSheet("background:transparent;")
        ctrl_lay = QVBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(0, 10, 0, 10)
        ctrl_lay.setSpacing(5)
        ctrl_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._header_plc = QLabel("PLC: -")
        self._header_plc.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-weight:600;background:transparent;"
        )
        ctrl_lay.addWidget(self._header_plc)

        reconnect_btn = QPushButton("Reconectar PLC")
        reconnect_btn.setFixedHeight(22)
        reconnect_btn.setStyleSheet(
            "background:#1e40af;color:white;border-radius:5px;"
            "font-size:10px;padding:0 8px;border:none;"
        )
        reconnect_btn.clicked.connect(self._reconnect_plc)
        ctrl_lay.addWidget(reconnect_btn)

        right_lay.addStretch()
        right_lay.addWidget(ctrl)

        dm_logo = _logo_label("logos/defymotion.jpg", 48)
        dm_logo.setContentsMargins(6, 0, 0, 0)
        right_lay.addWidget(dm_logo)

        outer.addWidget(right_wing)

        return header

    # ------------------------------------------------------------------

    def _reconnect_plc(self) -> None:
        ok = self._system.connect_plc()
        logger.info(f"[Servicio] Reconectar PLC -> {'OK' if ok else 'FALLO'}")

    def _on_tab_changed(self, _idx: int) -> None:
        if self._tabs.currentWidget() is self._events_tab:
            self._events_tab.reload()

    def _refresh(self) -> None:
        connected = self._system.plc.connected
        if connected != self._last_plc_connected:
            self._last_plc_connected = connected
            self._header_plc.setText(
                "PLC: Conectado" if connected else "PLC: Desconectado"
            )
            self._header_plc.setStyleSheet(
                f"color:{_OK if connected else _NOK};"
                "font-size:11px;font-weight:600;background:transparent;"
            )
        # Health bar — siempre actualizada
        self._health_bar.refresh()

        idx = self._tabs.currentIndex()
        if idx == 0:
            self._plc_tab.refresh()
        elif idx == 1:
            self._diag_tab.refresh()
        elif idx == 2:
            self._sys_tab.refresh()
        elif self._tabs.currentWidget() is self._events_tab:
            self._events_tab.refresh()
        # LogsTab se actualiza por señal; ConfigTab es estático

    def closeEvent(self, event) -> None:
        self._timer.stop()
        logging.getLogger().removeHandler(self._log_handler)
        # Detener workers de cámara IP antes de destruir los widgets para evitar
        # que threads activos emitan señales a objetos Qt ya eliminados (crash intermitente).
        try:
            self._rec_tab._on_ip_disconnect()
        except Exception:
            pass
        try:
            for slot in range(2):
                self._cam_tab._disconnect_ip_slot(slot)
        except Exception:
            pass
        event.accept()


# ------------------------------------------------------------------
# Lanzador
# ------------------------------------------------------------------

def launch_service_ui(system: InspectionSystem) -> None:
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
        app.setWindowIcon(QIcon(icon_pix))
    win = ServiceWindow(system)
    win.show()
    app.exec()
