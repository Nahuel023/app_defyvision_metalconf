"""
Interfaz de servicio/calibraciÃ³n (PyQt6).

4 pestaÃ±as:
  PLC I/O       â€” tabla de seÃ±ales en tiempo real, toggle de salidas
  Sistema       â€” mÃ©tricas de sesiÃ³n por scanner + estado PLC
  Logs          â€” visor de logs Python en tiempo real
  ConfiguraciÃ³n â€” visualizaciÃ³n read-only de archivos YAML

Se lanza tras autenticaciÃ³n con LoginDialog.
Acepta un InspectionSystem existente (desde OperatorWindow) o crea uno propio.
"""

import json
import logging
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
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
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

# Ancho fijo de cada ala del header â€” igualar ambos lados centra el tÃ­tulo
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
# Qt logging handler
# ==================================================================

class _LogEmitter(QObject):
    record = pyqtSignal(str, int)   # (formatted_message, levelno)


class QtLogHandler(logging.Handler):
    """ReenvÃ­a registros al widget de logs mediante seÃ±al Qt (thread-safe)."""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _LogEmitter()
        self.signal   = self._emitter.record
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s â€” %(message)s",
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
    """Tabla de seÃ±ales PLC con lectura en vivo y toggle de salidas."""

    _COLS = ["Scanner", "SeÃ±al", "Tipo", "Valor", "AcciÃ³n"]

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
        lbl = QLabel("Estado de seÃ±ales PLC en tiempo real")
        lbl.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        top.addWidget(lbl)
        top.addStretch()
        self._plc_status = QLabel("PLC: â€”")
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

            val_item = QTableWidgetItem("â€”")
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
                item.setText("â€”")
                item.setForeground(QBrush(QColor(_MUTED)))
            else:
                item.setText("ON" if val else "OFF")
                color = (_OK if sig_type == "output" else _ACCENT) if val else _MUTED
                item.setForeground(QBrush(QColor(color)))

    def _toggle_output(self, name: str) -> None:
        current = self._system.io.read(name)
        new_val = not bool(current)
        self._system.io.write(name, new_val)
        logger.info(f"[Servicio] Toggle {name} â†’ {'ON' if new_val else 'OFF'}")


# ==================================================================
# Tab: DiagnÃ³stico HW â€” X0-X15 / Y0-Y15
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
                    continue  # botÃ³n bloqueado, no actualizar estilo
                self._y_btns[i].setText("ON" if v else "OFF")
                self._y_btns[i].setStyleSheet(
                    f"background:{'#c2410c' if v else '#374151'};"
                    "color:white;border-radius:4px;font-size:10px;font-weight:700;border:none;"
                )

    def _toggle(self, idx: int) -> None:
        self._plc.write_coil(idx, not self._y_vals[idx])
        logger.info(f"[DiagnÃ³stico] Toggle Y{idx} â†’ {'ON' if not self._y_vals[idx] else 'OFF'}")


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

        title = QLabel("Prueba de salidas PLC â€” activar cada salida manualmente para verificar")
        title.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        warn = QLabel(
            "PrecauciÃ³n: los cambios aquÃ­ escriben directamente al PLC "
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
                    btn.setToolTip("Solenoide bloqueado por seguridad\n(activaciÃ³n por software deshabilitada)")
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
        logger.info(f"[PruebaS] {scanner_id}.{sig_key} â†’ {'ON' if new_val else 'OFF'}")

    def _all_off(self, scanner_id: str) -> None:
        for sig_key in self._btns.get(scanner_id, {}):
            self._system.io.write(f"{scanner_id}.{sig_key}", False)
        logger.info(f"[PruebaS] {scanner_id} â€” Todo OFF")

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
    """MÃ©tricas de sesiÃ³n por scanner y estado general del sistema."""

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

        ip_w, self._plc_ip_lbl   = self._kv("IP / Puerto", "â€”")
        conn_w, self._plc_conn_lbl = self._kv("Estado",     "â€”")
        poll_w, self._plc_poll_lbl = self._kv("Poll interval", "â€”")
        for w in (ip_w, conn_w, poll_w):
            plc_lay.addWidget(w)
        plc_lay.addStretch()
        lay.addWidget(plc_group)

        # Per-scanner groups
        _FIELDS = [
            ("state",             "Estado"),
            ("mode",              "Modo"),
            ("nok_streak",        "Racha NOK actual"),
            ("max_nok_streak",    "Racha NOK mÃ¡x."),
            ("total_inspections", "Total inspecciones"),
            ("ok_count",          "OK"),
            ("nok_count",         "NOK"),
            ("session_start",     "Inicio de sesiÃ³n"),
            ("camera",            "CÃ¡mara"),
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
                w, lbl = self._kv(label, "â€”")
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

            start_btn = _btn("â–¶  Iniciar",  "#166534")
            stop_btn  = _btn("â–   Detener",  "#7f1d1d")
            reset_btn = _btn("â†º  Reset",    "#1e3a5f")
            sim_btn   = _btn("âš¡  Forzar InspecciÃ³n", "#78350f")
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
                start.strftime("%H:%M:%S") if start else "â€”"
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
# Tab 4: ConfiguraciÃ³n
# ==================================================================

class ConfigTab(QWidget):
    """VisualizaciÃ³n read-only de archivos YAML de configuraciÃ³n."""

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
# Tab 5: GrabaciÃ³n / AnÃ¡lisis / Navegador
# ==================================================================

class _AnalysisWorker(QThread):
    progress = pyqtSignal(int, int)          # (done, total)
    finished = pyqtSignal(list)              # list[InspectionResult]
    error    = pyqtSignal(str)

    def __init__(self, model: str, frame_paths: list, scanner_id: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._model      = model
        self._paths      = frame_paths
        self._scanner_id = scanner_id

    def run(self) -> None:
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from src.inspection import inspect_image
        from src.patterns.pattern_io import load_pattern, find_pattern_path
        from src.patterns.roi import load_roi
        from src.utils.config import load_tolerances
        from src.pipeline.machine_stop import MachineStopDetector

        try:
            n = len(self._paths)
            tols = load_tolerances(self._model)

            # Pre-load shared read-only resources once (eliminates NÃ—3 disk reads).
            _pre: dict = {
                "tolerances": tols,
                "pattern":    load_pattern(find_pattern_path(self._model, self._scanner_id)),
                "roi":        load_roi(self._model, self._scanner_id),
            }

            results: list = [None] * n

            machine_stop_enabled = bool(tols.get("machine_stop_enabled", False))

            if machine_stop_enabled:
                # Sequential processing required: MachineStopDetector is stateful and
                # must see frames in order to accumulate streaks correctly.
                ms_det = MachineStopDetector(
                    enabled=True,
                    missing_frames=int(tols.get("machine_stop_missing_frames", 5)),
                    min_missing=int(tols.get("machine_stop_min_missing", 1)),
                    same_zone_px=float(tols.get("machine_stop_same_zone_px", 35.0)),
                    ignore_near_miss=bool(tols.get("machine_stop_ignore_near_miss", True)),
                    track_by_grid=bool(tols.get("machine_stop_track_by_grid", True)),
                    same_column_tol_cells=int(tols.get("machine_stop_same_column_tol_cells", 0)),
                )
                _pre["machine_stop_detector"] = ms_det
                # Emit progress at most every ~2% of total frames to avoid flooding
                # the main thread event loop with cross-thread signal deliveries.
                _emit_every = max(1, n // 50)
                for i, path in enumerate(self._paths):
                    results[i] = inspect_image(
                        self._model, path,
                        scanner_id=self._scanner_id,
                        _preloaded=_pre,
                    )
                    if (i + 1) % _emit_every == 0 or i + 1 == n:
                        self.progress.emit(i + 1, n)
            else:
                done = 0
                _emit_every = max(1, n // 50)
                # OpenCV and numpy release the GIL â€” parallel is safe when no stateful detector.
                n_workers = min(os.cpu_count() or 2, 6)

                def _worker(args):
                    idx, path = args
                    return idx, inspect_image(
                        self._model, path,
                        scanner_id=self._scanner_id,
                        _preloaded=_pre,
                    )

                with ThreadPoolExecutor(max_workers=n_workers) as executor:
                    futures = {
                        executor.submit(_worker, (i, p)): i
                        for i, p in enumerate(self._paths)
                    }
                    for future in as_completed(futures):
                        idx, result = future.result()
                        results[idx] = result
                        done += 1
                        if done % _emit_every == 0 or done == n:
                            self.progress.emit(done, n)

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
    JPEG (SOI 0xFF 0xD8 â€¦ EOI 0xFF 0xD9) directamente en el flujo de bytes.
    Funciona con cÃ¡maras Axis y cualquier stream MJPEG estÃ¡ndar.
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
                # Scan for JPEG SOI â€¦ EOI boundaries
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


class RecordingTab(QWidget):
    """GrabaciÃ³n continua de frames, anÃ¡lisis offline y navegador de resultados."""

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system       = system
        self._recording    = False
        self._rec_dir: Optional[Path] = None
        self._frame_paths: list[Path] = []
        self._results: list           = []
        self._current_idx: int        = 0
        self._worker: Optional[_AnalysisWorker] = None
        self._live_ms_detector = None   # persistent detector for live inspection mode

        # PNG writes go to a background thread so the main thread stays responsive.
        self._write_executor = None   # concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # QPixmap cache: (idx, overlay_bool) â†’ QPixmap.  Avoids re-converting BGRâ†’QPixmap
        # on every navigation click. Cleared when a new analysis/load replaces the data.
        self._px_cache: dict = {}
        self._px_cache_max = 40  # keep last ~40 pixmaps (~320 MB for 1920Ã—1080)

        # Track last result-card state to skip redundant setStyleSheet calls.
        self._last_card_state: str | None = None

        # Indices of NOK frames for quick navigation.
        self._nok_indices: list[int] = []

        # IP camera live view â€” MJPEG worker (HTTP) or cv2 fallback (RTSP/USB)
        self._ip_worker: Optional[_MJPEGReader] = None
        self._ip_cap:    Optional[cv2.VideoCapture] = None
        self._ip_timer = QTimer(self)
        self._ip_timer.timeout.connect(self._refresh_ip_camera)

        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._grab_frame)
        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{_DARK}; }}")
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_recording_section())
        root.addWidget(self._build_analysis_section())
        root.addWidget(self._build_browser_section(), stretch=1)

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

        self._update_nav_state()
        self._update_model_chip(self._model_combo.currentText())
        self._set_rec_badge("standby", 0, None)

    def _build_recording_section(self) -> QGroupBox:
        grp = QGroupBox("GRABACIÃ“N")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(12)

        # â”€â”€ Config row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cfg = QHBoxLayout()
        cfg.setSpacing(0)

        def _chip(label: str) -> QLabel:
            l = QLabel(label)
            l.setStyleSheet(
                f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;"
                f"background:{_DARK};border:1px solid {_BORDER};"
                "border-radius:4px;padding:2px 8px;margin-right:4px;"
            )
            return l

        cfg.addWidget(_chip("SCANNER"))
        self._scanner_combo = self._make_combo(self._system.scanner_ids(), min_w=90)
        self._scanner_combo.currentTextChanged.connect(self._on_scanner_changed)
        cfg.addWidget(self._scanner_combo)

        cfg.addSpacing(16)

        # â”€â”€ Model selector: two large toggle buttons (exclusive) â”€â”€â”€â”€â”€â”€
        self._btn_model_esterilla = QPushButton("â— ESTERILLA")
        self._btn_model_esterilla.setCheckable(True)
        self._btn_model_esterilla.setFixedHeight(38)
        self._btn_model_esterilla.setMinimumWidth(148)
        self._btn_model_microperf = QPushButton("â— MICROPERFORADO")
        self._btn_model_microperf.setCheckable(True)
        self._btn_model_microperf.setFixedHeight(38)
        self._btn_model_microperf.setMinimumWidth(178)

        self._model_btn_group = QButtonGroup(self)
        self._model_btn_group.setExclusive(True)
        self._model_btn_group.addButton(self._btn_model_esterilla, 0)
        self._model_btn_group.addButton(self._btn_model_microperf, 1)

        # Hidden combo keeps all downstream logic intact
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

        cfg.addWidget(self._btn_model_esterilla)
        cfg.addSpacing(4)
        cfg.addWidget(self._btn_model_microperf)
        cfg.addWidget(self._model_combo)  # hidden; kept in layout for enable/disable cycle

        cfg.addSpacing(12)
        cfg.addWidget(_chip("FPS"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(10)
        self._fps_spin.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:4px 6px;font-size:12px;max-width:66px;"
        )
        cfg.addWidget(self._fps_spin)

        cfg.addSpacing(16)
        self._live_chk = QCheckBox("AnÃ¡lisis en vivo")
        self._live_chk.setChecked(False)
        self._live_chk.setStyleSheet(f"color:{_TEXT};font-size:12px;")
        cfg.addWidget(self._live_chk)

        cfg.addStretch()

        # Camera info inline
        self._btn_read_cam = QPushButton("â†»  Info cÃ¡mara")
        self._btn_read_cam.setFixedHeight(30)
        self._btn_read_cam.setStyleSheet(
            f"QPushButton {{ background:{_PANEL};color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:6px;font-size:10px;font-weight:600;padding:0 12px; }}"
            f"QPushButton:hover {{ color:{_TEXT};border-color:#64748b; }}"
        )
        cfg.addWidget(self._btn_read_cam)
        cfg.addSpacing(8)
        self._cam_info_lbl = QLabel("â€”")
        self._cam_info_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas,monospace;"
        )
        cfg.addWidget(self._cam_info_lbl)

        lay.addLayout(cfg)

        # â”€â”€ Action row: buttons + state badge â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        act = QHBoxLayout()
        act.setSpacing(10)

        self._btn_start = self._mk_btn("â–¶  INICIAR GRABACIÃ“N", "#15803d", h=42, fs=13)
        self._btn_stop  = self._mk_btn("â–   DETENER",           "#991b1b", h=42, fs=13)
        self._btn_stop.setEnabled(False)
        act.addWidget(self._btn_start)
        act.addWidget(self._btn_stop)
        act.addStretch()

        # State badge â€” prominent indicator panel
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
        self._rec_state_lbl = QLabel("â— STANDBY")
        self._rec_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_state_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-weight:700;"
            "letter-spacing:3px;background:transparent;"
        )
        self._rec_folder_lbl = QLabel("â€”")
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

    def _build_ip_camera_section(self) -> QGroupBox:
        grp = QGroupBox("CÃMARA IP EN VIVO")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(8)

        # â”€â”€ URL row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        self._ip_url_edit.setText("http://192.168.1.17/axis-cgi/mjpg/video.cgi")
        self._ip_url_edit.setPlaceholderText("http://ip/mjpg/video.cgi  o  rtsp://ip:554/axis-media/media.amp  o  0 (USB)")
        self._ip_url_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:6px;padding:6px 10px;font-size:12px;font-family:Consolas,monospace;"
            f"selection-background-color:{_ACCENT};"
        )
        self._ip_url_edit.returnPressed.connect(self._on_ip_connect)
        url_row.addWidget(self._ip_url_edit, stretch=1)

        self._btn_ip_connect = self._mk_btn("â–¶  Conectar",    "#15803d", h=36, fs=12)
        self._btn_ip_disconnect = self._mk_btn("â–   Desconectar", "#991b1b", h=36, fs=12)
        self._btn_ip_disconnect.setEnabled(False)
        self._btn_ip_connect.clicked.connect(self._on_ip_connect)
        self._btn_ip_disconnect.clicked.connect(self._on_ip_disconnect)
        url_row.addWidget(self._btn_ip_connect)
        url_row.addWidget(self._btn_ip_disconnect)

        self._ip_status_lbl = QLabel("â€”")
        self._ip_status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
        )
        url_row.addWidget(self._ip_status_lbl)

        lay.addLayout(url_row)

        # â”€â”€ Preview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._ip_preview = QLabel("Sin seÃ±al â€” ingrese la URL de la cÃ¡mara y presione Conectar")
        self._ip_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ip_preview.setMinimumHeight(220)
        self._ip_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._ip_preview.setStyleSheet(
            f"background:#0a0f1a;border-radius:8px;border:1px solid {_BORDER};"
            f"color:{_MUTED};font-size:12px;"
        )
        lay.addWidget(self._ip_preview)

        return grp

    def _on_ip_connect(self) -> None:
        url = self._ip_url_edit.text().strip()
        if not url:
            self._ip_status_lbl.setText("âš   Ingrese una URL")
            return
        self._on_ip_disconnect()
        self._ip_status_lbl.setText("Conectandoâ€¦")
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
            self._ip_status_lbl.setText("âš   No se pudo conectar")
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
        self._ip_preview.setText("Sin seÃ±al")
        self._btn_ip_connect.setEnabled(True)
        self._btn_ip_disconnect.setEnabled(False)
        self._ip_url_edit.setEnabled(True)
        self._ip_status_lbl.setText("â€”")

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
        w = max(320, rect.width() - 4)
        h = max(180, rect.height() - 4)
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
        grp = QGroupBox("ANÃLISIS")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(14, 20, 14, 14)
        lay.setSpacing(10)

        # Row 1: action buttons + progress
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self._btn_load    = self._mk_btn("ðŸ“‚  Abrir grabaciÃ³n", "#374151", h=36, fs=11)
        self._btn_analyze = self._mk_btn("âš™  Analizar",         "#1d4ed8", h=36, fs=12)
        self._btn_analyze.setEnabled(False)
        row1.addWidget(self._btn_load)
        row1.addWidget(self._btn_analyze)
        row1.addSpacing(12)

        self._ana_progress = QLabel("")
        self._ana_progress.setStyleSheet(
            f"color:{_ACCENT};font-size:11px;font-family:Consolas;font-weight:600;"
        )
        row1.addWidget(self._ana_progress)
        row1.addStretch()

        self._status_lbl = QLabel("Listo")
        self._status_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
        )
        row1.addWidget(self._status_lbl)

        lay.addLayout(row1)


        # Row 2: results summary as stat chips
        self._summary_row = QHBoxLayout()
        self._summary_row.setSpacing(8)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-weight:700;letter-spacing:0.5px;"
        )
        self._summary_row.addWidget(self._summary_lbl)
        self._summary_row.addStretch()

        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas;"
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

        # â”€â”€ Navigation bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            if w:
                b.setFixedWidth(w)
            b.setStyleSheet(_NAV_BASE.format(
                bg=bg, fg=fg, bd=bd, hv=hv, fs=fs, pad=pad
            ))
            return b

        # First / Last â€” outlined style
        self._btn_first = _nav_btn("â®", w=40, tooltip="Primer frame", pad=0)
        self._btn_last  = _nav_btn("â­", w=40, tooltip="Ãšltimo frame",  pad=0)

        # Â±10 â€” larger with text
        self._btn_prev10 = _nav_btn("Â«  âˆ’10", w=74, tooltip="Retroceder 10 frames",
                                     bg="#1e293b", bd="#334155", hv="#2d3f55", fs=12)
        self._btn_next10 = _nav_btn("+10  Â»", w=74, tooltip="Avanzar 10 frames",
                                     bg="#1e293b", bd="#334155", hv="#2d3f55", fs=12)

        # Â±1 â€” filled accent style
        self._btn_prev = _nav_btn("â—€  Ant.", w=76, tooltip="Frame anterior",
                                   bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8", fs=12)
        self._btn_next = _nav_btn("Sig.  â–¶", w=76, tooltip="Frame siguiente",
                                   bg="#1e3a5f", bd="#2563eb", hv="#1d4ed8", fs=12)

        # Frame counter
        self._nav_lbl = QLabel("â€”")
        self._nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_lbl.setMinimumWidth(130)
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

        # â”€â”€ NOK navigator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._btn_prev_nok = _nav_btn("â—€ NOK", w=72, tooltip="Frame NOK anterior",
                                      bg="#3b0f0f", bd="#7f1d1d", hv="#5c1515", fs=11)
        self._btn_next_nok = _nav_btn("NOK â–¶", w=72, tooltip="Frame NOK siguiente",
                                      bg="#3b0f0f", bd="#7f1d1d", hv="#5c1515", fs=11)
        self._nok_nav_lbl = QLabel("NOK â€”")
        self._nok_nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nok_nav_lbl.setFixedWidth(80)
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

        # Overlay toggle â€” pill style
        self._overlay_toggle = QPushButton("â—‰  OVERLAY")
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

        self._btn_fit = _nav_btn("âŠž  Ajustar", bg="#0f3460", bd="#1d4ed8", hv="#1e40af",
                                  tooltip="Ajustar imagen a ventana (doble clic en imagen)")
        nav.addWidget(self._btn_fit)
        nav.addStretch()

        # â”€â”€ Model chip â€” shows which pattern type was used for analysis â”€
        self._model_chip = QLabel("â€”")
        self._model_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._model_chip.setFixedHeight(38)
        self._model_chip.setMinimumWidth(140)
        self._model_chip.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-weight:700;letter-spacing:2px;"
            f"background:{_DARK};border:1px solid {_BORDER};"
            "border-radius:7px;padding:0 14px;"
        )
        nav.addSpacing(8)
        nav.addWidget(self._model_chip)
        nav.addSpacing(8)

        # â”€â”€ Result card â€” right of nav bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._result_card = QFrame()
        self._result_card.setMinimumWidth(280)
        self._result_card.setStyleSheet(
            f"QFrame {{ background:{_PANEL};border:1px solid {_BORDER};border-radius:8px; }}"
        )
        rc_lay = QHBoxLayout(self._result_card)
        rc_lay.setContentsMargins(14, 0, 14, 0)
        self._result_lbl = QLabel("â€”")
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_lbl.setStyleSheet(f"font-size:13px;font-weight:700;color:{_MUTED};")
        rc_lay.addWidget(self._result_lbl)
        nav.addWidget(self._result_card)

        lay.addLayout(nav)

        # â”€â”€ Separator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        lay.addWidget(self._hline())

        # â”€â”€ Save / Export row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        save = QHBoxLayout()
        save.setSpacing(10)

        self._btn_save_current = self._mk_btn("ðŸ’¾  Guardar frame", "#065f46", h=36, fs=11)
        self._btn_save_current.setEnabled(False)
        self._btn_save_current.setToolTip("Guarda el overlay del frame actual en data/output/export/")
        save.addWidget(self._btn_save_current)

        save.addWidget(self._vline())

        # Export group â€” visual container
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

        self._btn_export = self._mk_btn("â¬‡  Exportar 0 frames", "#0f3460", h=36, fs=11)
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

        # â”€â”€ Separator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        lay.addWidget(self._hline())

        # â”€â”€ Image viewer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._img_view = ZoomableImageView("Sin frames")
        self._img_view.setMinimumHeight(600)
        lay.addWidget(self._img_view, stretch=1)

        return grp

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        sid = self._scanner_combo.currentText()
        cam = self._system.camera(sid)
        if not cam.is_running:
            self._status_lbl.setText("âš   La cÃ¡mara no estÃ¡ activa")
            return

        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        self._rec_dir = Path("data/recordings") / ts
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
        self._status_lbl.setText(f"Grabando{mode_txt} â†’ {self._rec_dir.name}")
        self._set_rec_badge("recording", 0, self._rec_dir)
        logger.info(f"[GrabaciÃ³n] inicio en {self._rec_dir}  modelo={meta['model_display']}  fps={meta['fps']}")

    def _on_stop(self) -> None:
        self._rec_timer.stop()
        self._recording = False
        self._live_ms_detector = None
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
        self._status_lbl.setText(f"Detenido â€” {n} frames en {self._rec_dir.name}")
        self._set_rec_badge("ready", n, self._rec_dir)
        self._btn_analyze.setEnabled(n > 0)
        self._update_export_range_max()
        if n > 0 and not self._results:
            self._show_frame(0)
        logger.info(f"[GrabaciÃ³n] detenida â€” {n} frames")

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

        # Write PNG in background â€” PNG compression can take 50-200ms and must not
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
                from src.inspection import inspect_image
                from src.utils.config import load_tolerances
                from src.pipeline.machine_stop import MachineStopDetector

                model      = self._active_model()
                scanner_id = self._scanner_combo.currentText() or None

                # Create a persistent detector once per recording session.
                if self._live_ms_detector is None:
                    tols = load_tolerances(model)
                    if bool(tols.get("machine_stop_enabled", False)):
                        self._live_ms_detector = MachineStopDetector(
                            enabled=True,
                            missing_frames=int(tols.get("machine_stop_missing_frames", 5)),
                            min_missing=int(tols.get("machine_stop_min_missing", 1)),
                            same_zone_px=float(tols.get("machine_stop_same_zone_px", 35.0)),
                            ignore_near_miss=bool(tols.get("machine_stop_ignore_near_miss", True)),
                            track_by_grid=bool(tols.get("machine_stop_track_by_grid", True)),
                            same_column_tol_cells=int(tols.get("machine_stop_same_column_tol_cells", 0)),
                        )

                _pre_live: dict = {}
                if self._live_ms_detector is not None:
                    _pre_live["machine_stop_detector"] = self._live_ms_detector

                result = inspect_image(model, path, scanner_id=scanner_id,
                                       _preloaded=_pre_live if _pre_live else None)
                self._results.append(result)
                ok  = sum(1 for r in self._results if r.status == "OK")
                nok = len(self._results) - ok
                self._summary_lbl.setText(f"OK: {ok}  NOK: {nok}  Total: {len(self._results)}")
                self._show_frame(idx)
            except Exception as exc:
                logger.error(f"[Live anÃ¡lisis] error en frame {idx}: {exc}")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _on_analyze(self) -> None:
        if not self._frame_paths:
            return
        # Flush any background PNG writes before reading the files for analysis.
        if self._write_executor is not None:
            self._write_executor.shutdown(wait=True)
            self._write_executor = None
        self._px_cache.clear()
        self._last_card_state = None
        self._btn_analyze.setEnabled(False)
        self._results.clear()
        self._stats_lbl.setText("")
        self._export_status_lbl.setText("")
        self._ana_progress.setText("Analizandoâ€¦")
        self._set_rec_badge("analyzing", len(self._frame_paths), self._rec_dir)

        self._worker = _AnalysisWorker(
            self._active_model(), list(self._frame_paths),
            scanner_id=self._scanner_combo.currentText() or None,
            parent=self,
        )
        self._worker.progress.connect(self._on_ana_progress)
        self._worker.finished.connect(self._on_ana_done)
        self._worker.error.connect(self._on_ana_error)
        self._worker.start()

    def _on_ana_progress(self, done: int, total: int) -> None:
        self._ana_progress.setText(f"Analizando  {done} / {total}â€¦")

    def _on_ana_done(self, results: list) -> None:
        self._results = results
        ok  = sum(1 for r in results if r.status == "OK")
        nok = len(results) - ok
        pct = round(100 * ok / len(results)) if results else 0

        from src.inspection import _apply_temporal_rule
        from src.utils.config import load_tolerances
        model  = self._active_model()
        tols   = load_tolerances(model)
        consec = int(tols.get("consecutive_nok_frames", 5))
        temporal = _apply_temporal_rule(results, consec)
        t_ok  = sum(1 for t in temporal if t.decision_status == "OK")
        t_nok = len(temporal) - t_ok
        t_pct = round(100 * t_ok / len(temporal)) if temporal else 0

        ok_color  = _OK  if ok  > 0 else _MUTED
        nok_color = _NOK if nok > 0 else _MUTED
        self._summary_lbl.setText(
            f"Frame  âœ“ OK: {ok} ({pct}%)   âœ— NOK: {nok}   Total: {len(results)}"
            f"    â”‚    Temporal âœ“ {t_ok} ({t_pct}%)  âœ— {t_nok}   [umbral {consec}]"
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

        self._ana_progress.setText("âœ“ AnÃ¡lisis completo")
        self._btn_analyze.setEnabled(True)
        self._set_rec_badge("analyzed", len(results), self._rec_dir)
        self._update_model_chip()
        self._update_export_range_max()
        self._rebuild_nok_index()
        self._show_frame(0)
        logger.info(f"[GrabaciÃ³n] anÃ¡lisis completo â€” OK={ok} NOK={nok}")

    def _on_ana_error(self, msg: str) -> None:
        self._ana_progress.setText(f"âš   Error: {msg}")
        self._btn_analyze.setEnabled(True)
        self._set_rec_badge("ready", len(self._frame_paths), self._rec_dir)
        logger.error(f"[GrabaciÃ³n] error de anÃ¡lisis: {msg}")

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _show_frame(self, idx: int) -> None:
        if not self._frame_paths:
            return
        idx = max(0, min(idx, len(self._frame_paths) - 1))
        self._current_idx = idx

        show_ov = self._overlay_toggle.isChecked() and idx < len(self._results)
        cache_key = (idx, show_ov)

        pxm = self._px_cache.get(cache_key)
        if pxm is None:
            if show_ov:
                bgr = self._results[idx].overlay
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

        total = len(self._frame_paths)
        self._nav_lbl.setText(f"{idx + 1} / {total}")

        if idx < len(self._results):
            r = self._results[idx]
            missing = len(r.report.missing_points)
            center_txt = ""
            if r.centering is not None:
                sign = "+" if r.centering.offset_px >= 0 else ""
                center_txt = f"  Â·  centro {sign}{r.centering.offset_px:.1f}px"

            holes_nok = r.report.status == "NOK"
            if r.status == "OK":
                label, color, card_border = "âœ“  OK", _OK, "#15803d"
            elif getattr(r, "centering_nok", False) and holes_nok:
                label, color, card_border = "âœ—  NOK  AGUJEROS+CENTRADO", _NOK, "#991b1b"
            elif getattr(r, "centering_nok", False):
                label, color, card_border = "âœ—  NOK  CENTRADO", "#f97316", "#92400e"
            else:
                label, color, card_border = "âœ—  NOK  AGUJEROS", _NOK, "#991b1b"

            miss_txt = f"  Â·  faltantes: {missing}" if missing else ""
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
            self._result_lbl.setText("â€”")

        self._btn_save_current.setEnabled(idx < len(self._results))
        self._update_nav_state()

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._overlay_toggle.setText("â— OVERLAY" if checked else "â—‹ OVERLAY")
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
        out_dir = Path("data/output/export")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts    = _dt.now().strftime("%Y%m%d_%H%M%S")
        fname = f"frame_{self._current_idx:04d}_{result.status}_{ts}.png"
        out_path = out_dir / fname
        cv2.imwrite(str(out_path), result.overlay)
        self._export_status_lbl.setText(f"âœ“  {fname}")
        logger.info(f"[Export] frame guardado â†’ {out_path}")

    def _update_export_label(self) -> None:
        if not self._frame_paths:
            return
        f_from = self._spin_from.value() - 1   # 0-based
        f_to   = self._spin_to.value()          # exclusive (1-based input = natural end)
        count  = max(0, f_to - f_from)
        has_results = len(self._results) >= f_to
        self._btn_export.setText(f"â¬‡  Exportar {count} frames")
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
        """Export overlays for selected frame range to data/output/export/."""
        import cv2
        from datetime import datetime as _dt
        if not self._results:
            QMessageBox.information(self, "Exportar", "Primero analice la grabaciÃ³n.")
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
            cv2.imwrite(str(out_dir / f"frame_{i:04d}_{r.status}.png"), r.overlay)
        saved = f_to - f_from
        self._export_status_lbl.setText(f"âœ“  {saved} frames â†’ export/rango_{ts}/")
        logger.info(f"[Export] {saved} frames â†’ {out_dir}")

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

    def _set_rec_badge(self, state: str, n_frames: int,
                       folder: Optional[Path]) -> None:
        """Update the prominent recording state indicator."""
        _STATES = {
            "standby":   (f"color:{_MUTED};",   "â— STANDBY"),
            "recording": ("color:#f87171;",      "â— GRABANDO"),
            "ready":     (f"color:{_OK};",       "â— LISTO"),
            "analyzing": (f"color:{_ACCENT};",   "â—Ž ANALIZANDO"),
            "analyzed":  (f"color:{_OK};",       "âœ“ ANALIZADO"),
        }
        style, text = _STATES.get(state, (f"color:{_MUTED};", "â— â€”"))
        self._rec_state_lbl.setStyleSheet(
            f"{style}font-size:13px;font-weight:700;letter-spacing:3px;background:transparent;"
        )
        self._rec_state_lbl.setText(text)
        self._rec_count_lbl.setText(str(n_frames))
        self._rec_folder_lbl.setText(folder.name if folder else "â€”")

    def _refresh_cam_info(self) -> None:
        sid = self._scanner_combo.currentText()
        try:
            cam = self._system.camera(sid)
        except Exception:
            self._cam_info_lbl.setText("cÃ¡mara no disponible")
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
            parts.append(f"{lbl}:{v:.0f}" if v >= 0 else f"{lbl}:?")
        fps_real = cam.fps
        parts.append(f"real:{fps_real:.1f}" if fps_real > 0 else "real:â€”")
        self._cam_info_lbl.setText("  ".join(parts))

    def _active_model(self) -> str:
        return to_internal(self._model_combo.currentText())

    # â”€â”€ Model toggle buttons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    _MODEL_BTN_STYLES = {
        "Esterilla": {
            "on":  ("background:#052e16;color:#4ade80;border-color:#16a34a;", "â— ESTERILLA"),
            "off": (f"background:#1e293b;color:#475569;border-color:#334155;", "â—‹ ESTERILLA"),
        },
        "Microperforado": {
            "on":  ("background:#0c2a3e;color:#38bdf8;border-color:#0284c7;", "â— MICROPERFORADO"),
            "off": (f"background:#1e293b;color:#475569;border-color:#334155;", "â—‹ MICROPERFORADO"),
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
        for name, btn in (("Esterilla", self._btn_model_esterilla),
                          ("Microperforado", self._btn_model_microperf)):
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

    def _on_scanner_changed(self, sid: str) -> None:
        if self._recording:
            return
        model_internal = self._system.io.scanner_config(sid).get("model", "")
        if model_internal:
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentText(to_display(model_internal))
            self._model_combo.blockSignals(False)
            self._sync_model_buttons()

    def _on_load_recording(self) -> None:
        """Load an existing recording folder for analysis."""
        base = str(Path("data/recordings").resolve())
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar grabaciÃ³n", base,
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
        self._results.clear()
        self._nok_indices  = []
        self._current_idx  = 0
        self._px_cache.clear()
        self._last_card_state = None
        self._nok_nav_lbl.setText("NOK â€”")
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
                model_display = meta.get("model_display", "")
                if model_display:
                    self._model_combo.setCurrentText(model_display)
                    self._sync_model_buttons()
                fps_saved = meta.get("fps")
                if fps_saved:
                    self._fps_spin.setValue(fps_saved)
                logger.info(f"[GrabaciÃ³n] meta cargada: {meta}")
            except Exception as exc:
                logger.warning(f"[GrabaciÃ³n] no se pudo leer meta.json: {exc}")

        self._btn_analyze.setEnabled(True)
        self._update_export_range_max()
        self._show_frame(0)
        self._set_rec_badge("ready", len(frames), folder_path)
        self._status_lbl.setText(f"Cargado â€” {len(frames)} frames  Â·  {folder_path.name}")
        logger.info(f"[GrabaciÃ³n] cargada carpeta {folder_path.name} con {len(frames)} frames")

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
        self._nok_nav_lbl.setText(f"NOK {total}" if total else "NOK â€”")

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
# CalibraciÃ³n de cÃ¡mara
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
    _ParamDef("exposure",               "ExposiciÃ³n",    -13,      0,  -7, "auto_exposure"),
    _ParamDef("white_balance",          "Bal. Blanco",  2800,   6500, 4500, "auto_white_balance"),
    _ParamDef("gain",                   "Ganancia",        0,    255,   0),
    _ParamDef("brightness",             "Brillo",          0,    255, 128),
    _ParamDef("contrast",               "Contraste",       0,    255, 140),
    _ParamDef("saturation",             "SaturaciÃ³n",      0,    255,  50),
    _ParamDef("sharpness",              "Nitidez",         0,    255, 160),
    _ParamDef("gamma",                  "Gamma",         100,    500, 110),
    _ParamDef("backlight_compensation", "Comp. backlight", 0,      1,   0),
]

# ParÃ¡metros para cÃ¡maras IP (rangos Axis VAPIX)
_IP_PARAM_DEFS: list[_ParamDef] = [
    _ParamDef("brightness",  "Brillo",        0, 100, 50),
    _ParamDef("contrast",    "Contraste",     0, 100, 50),
    _ParamDef("saturation",  "SaturaciÃ³n",    0, 100, 50),
    _ParamDef("sharpness",   "Nitidez",       0, 100, 50),
]

# Mapeo clave â†’ parÃ¡metro VAPIX (Axis)
_IP_VAPIX_MAP: dict[str, str] = {
    "brightness":  "ImageSource.I0.Sensor.Brightness",
    "contrast":    "ImageSource.I0.Sensor.Contrast",
    "saturation":  "ImageSource.I0.Sensor.ColorLevel",
    "sharpness":   "ImageSource.I0.Sensor.Sharpness",
}


class CameraCalibTab(QWidget):
    """Vista en vivo + sliders de todos los parÃ¡metros UVC de la cÃ¡mara."""

    def __init__(self, system: InspectionSystem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._system = system
        self._sliders:     dict[str, QSlider]   = {}
        self._spinboxes:   dict[str, QSpinBox]  = {}
        self._auto_checks: dict[str, QCheckBox] = {}
        self._block_apply = False  # avoid feedback loops while populating UI

        # IP camera state â€” two independent slots
        self._ip_workers: list[Optional[_MJPEGReader]] = [None, None]
        self._ip_caps:    list[Optional[cv2.VideoCapture]] = [None, None]
        self._ip_timers:  list[QTimer] = []
        for _i in range(2):
            _t = QTimer(self)
            _t.timeout.connect(lambda _s=_i: self._refresh_ip_camera(_s))
            self._ip_timers.append(_t)
        self._ip_slot = 0
        self._ip_param_sliders:   dict[str, QSlider]  = {}
        self._ip_param_spinboxes: dict[str, QSpinBox] = {}

        # Auto-reconectar, FPS y captura
        self._ip_retry_timers: list[QTimer] = []
        for _i in range(2):
            _rt = QTimer(self)
            _rt.setSingleShot(True)
            _rt.timeout.connect(lambda _s=_i: self._on_ip_retry(_s))
            self._ip_retry_timers.append(_rt)
        self._ip_retry_counts   = [0, 0]    # reintentos pendientes por slot
        self._ip_manual_disc    = [False, False]  # True = el operador desconectÃ³ manualmente
        self._ip_fps_count      = [0, 0]    # frames desde Ãºltimo cÃ¡lculo de FPS
        self._ip_fps_last_t     = [0.0, 0.0]
        self._ip_fps_value      = [0.0, 0.0]
        self._ip_last_res       = ["", ""]  # "WxH" del Ãºltimo frame
        self._ip_last_frames: list = [None, None]  # Ãºltimo frame BGR por slot

        self._ip_last_frame_t = [0.0, 0.0]
        self._ip_dropped_frames = [0, 0]
        self._ip_reconnect_total = [0, 0]
        self._ip_prev_sig = [None, None]
        self._ip_frozen_since = [0.0, 0.0]
        self._ip_frozen = [False, False]
        self._ip_last_diag_snapshot_t = [0.0, 0.0]

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.setInterval(200)

        self._build_ui()
        self._populate_scanner_selector()

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

        # â”€â”€ top bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        self._cam_btn = QPushButton("Iniciar cÃ¡mara")
        self._cam_btn.setFixedWidth(140)
        self._cam_btn.clicked.connect(self._toggle_camera)
        top.addWidget(self._cam_btn)

        self._read_btn = QPushButton("Leer de cÃ¡mara")
        self._read_btn.setFixedWidth(140)
        self._read_btn.clicked.connect(self._read_from_camera)
        top.addWidget(self._read_btn)

        top.addStretch()

        self._status_lbl = QLabel("â€”")
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(self._status_lbl)

        root.addLayout(top)
        root.addWidget(self._build_ip_camera_section(), stretch=2)

        # â”€â”€ main split â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        main = QHBoxLayout()
        main.setSpacing(12)

        # Left: live preview
        prev_grp = QGroupBox("Vista en vivo")
        prev_grp.setStyleSheet(self._grp_style())
        prev_grp.setMinimumWidth(560)
        prev_lay = QVBoxLayout(prev_grp)

        self._preview_lbl = QLabel("Sin seÃ±al de cÃ¡mara")
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
        params_grp = QGroupBox("ParÃ¡metros de cÃ¡mara")
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
        save_btn = QPushButton("Guardar configuraciÃ³n")
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
        grp = QGroupBox("CÃ¡maras IP")
        grp.setStyleSheet(self._grp_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(18, 24, 18, 18)
        lay.setSpacing(10)

        _field_ss = (
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 9px;font-size:12px;"
        )
        _lbl_ss = f"color:{_MUTED};font-size:11px;"

        # â”€â”€ Fila 1: selector + URL + botones â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self._ip_slot_combo = QComboBox()
        self._ip_slot_combo.addItems(["CÃ¡mara IP 1", "CÃ¡mara IP 2"])
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
        self._ip_host_edit.setPlaceholderText("192.168.1.17")
        self._ip_host_edit.setStyleSheet(
            f"background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 10px;font-size:12px;"
            f"font-family:Consolas,monospace;selection-background-color:{_ACCENT};"
        )
        self._ip_host_edit.returnPressed.connect(self._on_ip_connect)
        self._ip_host_edit.setPlaceholderText("192.168.1.17")
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

        self._ip_status_lbl = QLabel("â€”")
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
        url_lbl = QLabel("URL generada")
        url_lbl.setStyleSheet(_lbl_ss)
        url_row.addWidget(url_lbl)

        self._ip_url_edit = QLineEdit()
        self._ip_url_edit.setFixedHeight(30)
        self._ip_url_edit.setReadOnly(True)
        self._ip_url_edit.setPlaceholderText("Se completa automaticamente desde la IP")
        self._ip_url_edit.setStyleSheet(
            f"background:#111827;color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:5px;padding:4px 9px;font-size:11px;"
            "font-family:Consolas,monospace;"
        )
        url_row.addWidget(self._ip_url_edit, stretch=1)
        lay.addLayout(url_row)

        # â”€â”€ Fila 2: credenciales â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        p_lbl = QLabel("ContraseÃ±a")
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

        # â”€â”€ Separador â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_BORDER};background:{_BORDER};max-height:1px;")
        lay.addWidget(sep)

        # â”€â”€ ParÃ¡metros de imagen â€” grid 2Ã—2 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        lay.addSpacing(2)
        _sl_ss = (
            f"QSlider::groove:horizontal {{ height:3px;background:{_BORDER};border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ width:13px;height:13px;margin:-5px 0;"
            f"background:{_ACCENT};border-radius:7px; }}"
            f"QSlider::sub-page:horizontal {{ background:{_ACCENT};border-radius:2px; }}"
        )
        _sb_ss = (
            f"QSpinBox {{ background:{_DARK};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 4px;font-size:12px; }"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width:14px; }}"
        )

        # 4 parÃ¡metros en 2 columnas
        grid_lay = QHBoxLayout()
        grid_lay.setSpacing(20)
        col_left  = QVBoxLayout(); col_left.setSpacing(8)
        col_right = QVBoxLayout(); col_right.setSpacing(8)
        for i, pd in enumerate(_IP_PARAM_DEFS):
            col = col_left if i % 2 == 0 else col_right
            cell = QHBoxLayout(); cell.setSpacing(8)
            p_lbl2 = QLabel(pd.label)
            p_lbl2.setFixedWidth(82)
            p_lbl2.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p_lbl2.setStyleSheet(f"color:{_TEXT};font-size:12px;font-weight:600;")
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, pd.max_val - pd.min_val)
            sl.setValue(pd.default - pd.min_val)
            sl.setMinimumWidth(110)
            sl.setFixedHeight(22)
            sl.setStyleSheet(_sl_ss)
            sb = QSpinBox()
            sb.setRange(pd.min_val, pd.max_val)
            sb.setValue(pd.default)
            sb.setFixedWidth(72)
            sb.setFixedHeight(30)
            sb.setStyleSheet(_sb_ss)
            sl.valueChanged.connect(
                lambda v, _pd=pd, _sb=sb: (
                    _sb.blockSignals(True) or
                    _sb.setValue(_pd.min_val + v) or
                    _sb.blockSignals(False)
                )
            )
            sb.valueChanged.connect(
                lambda v, _pd=pd, _sl=sl: (
                    _sl.blockSignals(True) or
                    _sl.setValue(v - _pd.min_val) or
                    _sl.blockSignals(False)
                )
            )
            self._ip_param_sliders[pd.key] = sl
            self._ip_param_spinboxes[pd.key] = sb
            cell.addWidget(p_lbl2)
            cell.addWidget(sl, stretch=1)
            cell.addWidget(sb)
            col.addLayout(cell)
        grid_lay.addLayout(col_left,  stretch=1)
        grid_lay.addLayout(col_right, stretch=1)
        lay.addLayout(grid_lay)
        lay.addSpacing(4)

        # â”€â”€ Botones Guardar / Aplicar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        ip_save_btn = QPushButton("Guardar configuraciÃ³n")
        ip_save_btn.setFixedHeight(32)
        ip_save_btn.clicked.connect(self._save_ip_settings)
        ip_save_btn.setStyleSheet(
            f"QPushButton {{ background:{_ACCENT};color:#000;font-weight:700;"
            "border-radius:5px;padding:0 18px;font-size:12px; }}"
            "QPushButton:hover { background:#67e8f9; }"
        )

        ip_apply_btn = QPushButton("Aplicar a cÃ¡mara  (VAPIX / Axis)")
        ip_apply_btn.setFixedHeight(32)
        ip_apply_btn.clicked.connect(self._apply_ip_params)
        ip_apply_btn.setStyleSheet(
            f"QPushButton {{ background:{_PANEL};color:{_TEXT};"
            f"border:1px solid {_BORDER};"
            "border-radius:5px;padding:0 18px;font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{_ACCENT};color:{_ACCENT}; }}"
        )

        self._ip_apply_status = QLabel("")
        self._ip_apply_status.setStyleSheet(f"color:{_MUTED};font-size:11px;")

        btn_row.addWidget(ip_save_btn)
        btn_row.addWidget(ip_apply_btn)
        btn_row.addWidget(self._ip_apply_status)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # â”€â”€ Separador â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{_BORDER};background:{_BORDER};max-height:1px;")
        lay.addWidget(sep2)

        # â”€â”€ Preview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._ip_preview = QLabel("Sin seÃ±al  â€”  ingrese la URL y presione Conectar")
        self._ip_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ip_preview.setMinimumHeight(300)
        self._ip_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ip_preview.setStyleSheet(
            f"background:{_DARK};color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:8px;font-size:13px;"
        )
        lay.addWidget(self._ip_preview, stretch=1)

        # â”€â”€ Barra inferior: Capturar + info FPS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        self._ip_info_lbl = QLabel("â€”")
        self._ip_info_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:13px;font-weight:600;font-family:Consolas;"
        )
        bottom_row.addWidget(self._ip_info_lbl)
        bottom_row.addStretch()

        self._ip_capture_status = QLabel("")
        self._ip_capture_status.setStyleSheet(f"color:{_OK};font-size:11px;")
        bottom_row.addWidget(self._ip_capture_status)

        lay.addLayout(bottom_row)

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

        # Slider (mapped 0 â†’ max-min)
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

        # Bidirectional link slider â†” spinbox
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

        # Start with auto enabled â†’ manual controls disabled
        if pd.auto_key:
            slider.setEnabled(False)
            sb.setEnabled(False)

    # ------------------------------------------------------------------
    # Badge de estado IP â€” helper central
    # ------------------------------------------------------------------

    _STATUS_STYLES = {
        "ok":      ("#22c55e", "#052e16", "#16a34a"),   # text, bg, border
        "warn":    ("#f59e0b", "#1c1507", "#b45309"),
        "error":   ("#f87171", "#1f0606", "#dc2626"),
        "neutral": (_MUTED,   _DARK,    _BORDER),
    }

    def _set_ip_status(self, text: str, kind: str = "neutral") -> None:
        """Actualiza el badge de estado con color semÃ¡ntico y texto completo."""
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

    def _build_ip_camera_url(self, value: str) -> str:
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
        return f"http://{host}/axis-cgi/mjpg/video.cgi?resolution=640x480&fps=10"

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
        url = self._build_ip_camera_url(self._ip_host_edit.text())
        self._ip_url_edit.setText(url)
        return url

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
            return False, "Solo soportado para camaras HTTP/Axis"

        headers = self._ip_basic_auth_header()
        endpoints = [
            f"{base}/axis-cgi/param.cgi?{query}",
            f"{base}/axis-cgi/admin/param.cgi?{query}",
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
                self._set_ip_status("Conectandoâ€¦", "warn")
            self._ip_info_lbl.setText(
                f"{res}  @  {fps:.0f} fps" if res and fps > 0 else "â€”"
            )
            self._btn_ip_capture.setEnabled(self._ip_last_frames[index] is not None)
        else:
            self._set_ip_status("â€”")
            self._ip_info_lbl.setText("â€”")
            self._btn_ip_capture.setEnabled(False)
            if hasattr(self, "_ip_preview"):
                self._ip_preview.setPixmap(QPixmap())
                self._ip_preview.setText("Sin seÃ±al")

    def _load_ip_slot_settings(self, slot: int) -> None:
        """Carga URL, credenciales y parÃ¡metros desde camera.yaml para el slot dado."""
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
        default_host = "192.168.1.17" if slot == 0 else ""
        host = self._extract_ip_camera_host(settings, default_host)
        self._ip_host_edit.setText(host)
        self._sync_ip_generated_url()
        self._ip_user_edit.setText(str(settings.get("username", "")))
        self._ip_pass_edit.setText(str(settings.get("password", "")))
        for pd in _IP_PARAM_DEFS:
            raw = int(settings.get(pd.key, pd.default))
            val = max(pd.min_val, min(pd.max_val, raw))
            self._ip_param_spinboxes[pd.key].setValue(val)

    # ------------------------------------------------------------------
    # ConexiÃ³n IP â€” lÃ³gica interna
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._auto_connect_if_saved()

    def _auto_connect_if_saved(self) -> None:
        """Conecta automÃ¡ticamente los slots con URL guardada que no fueron
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
                self._set_ip_status("Conectandoâ€¦", "warn")
                self._btn_ip_connect.setEnabled(False)
                self._btn_ip_disconnect.setEnabled(True)
                self._ip_host_edit.setEnabled(False)
                self._ip_url_edit.setEnabled(False)
                self._ip_user_edit.setEnabled(False)
                self._ip_pass_edit.setEnabled(False)
                self._ip_preview.setText("")

    def _start_ip_connection(self, slot: int, url: str,
                             user: "str | None", password: "str | None") -> None:
        """Inicia la conexiÃ³n para un slot sin modificar la UI."""
        self._disconnect_ip_slot(slot)
        self._ip_retry_counts[slot] = 0
        self._ip_prev_sig[slot] = None
        self._ip_frozen_since[slot] = 0.0
        self._ip_frozen[slot] = False
        source = int(url) if url.isdigit() else url
        if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
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

    def _on_ip_connect(self) -> None:
        slot = self._ip_slot
        url = self._sync_ip_generated_url()
        if not url:
            self._set_ip_status("Ingrese una IP", "warn")
            return
        self._ip_manual_disc[slot] = False
        self._set_ip_status("Conectandoâ€¦", "warn")
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
        self._disconnect_ip_slot(slot)
        if hasattr(self, "_ip_preview"):
            self._ip_preview.setPixmap(QPixmap())
            self._ip_preview.setText("Sin seÃ±al")
        self._btn_ip_connect.setEnabled(True)
        self._btn_ip_disconnect.setEnabled(False)
        self._ip_host_edit.setEnabled(True)
        self._ip_url_edit.setEnabled(True)
        self._ip_user_edit.setEnabled(True)
        self._ip_pass_edit.setEnabled(True)
        self._set_ip_status("â€”")
        self._btn_ip_capture.setEnabled(False)
        self._ip_info_lbl.setText("â€”")

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
        delay = min(5 + self._ip_retry_counts[slot] * 5, 30)
        self._ip_retry_counts[slot] += 1
        self._ip_retry_timers[slot].start(delay * 1000)
        self._save_ip_diagnostic_snapshot(slot, f"error_{msg}")
        if slot == self._ip_slot:
            n = self._ip_retry_counts[slot]
            self._set_ip_status(f"Reintento {n} â€” en {delay}s", "error")
            self._btn_ip_connect.setEnabled(False)
            self._btn_ip_disconnect.setEnabled(True)
            self._btn_ip_capture.setEnabled(False)

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
        self._start_ip_connection(slot, url, user, password)
        if slot == self._ip_slot:
            n = self._ip_retry_counts[slot]
            self._set_ip_status(f"Intentando conectarâ€¦ ({n})", "warn")

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
        self._ip_last_res[slot] = f"{fw}Ã—{fh}"
        if slot == self._ip_slot:
            frozen = self._update_ip_freeze_watchdog(frame, slot, now)
            fps_str = (f"{self._ip_fps_value[slot]:.0f} fps"
                       if self._ip_fps_value[slot] > 0 else "â€¦")
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
                self._on_ip_error("seÃ±al perdida", slot)
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
            # Limpiar el mensaje despuÃ©s de 4 segundos
            QTimer.singleShot(4000, lambda: self._ip_capture_status.setText(""))
        except Exception as exc:
            self._ip_capture_status.setStyleSheet(f"color:{_NOK};font-size:11px;")
            self._ip_capture_status.setText(f"Error: {exc}")

    def _save_ip_settings(self) -> None:
        slot = self._ip_slot
        key = f"ip_camera_{slot + 1}"
        url = self._sync_ip_generated_url()
        settings: dict = {
            "ip_address": self._ip_host_edit.text().strip(),
            "url":      url,
            "username": self._ip_user_edit.text().strip(),
            "password": self._ip_pass_edit.text().strip(),
        }
        for pd in _IP_PARAM_DEFS:
            settings[pd.key] = self._ip_param_spinboxes[pd.key].value()
        try:
            camera_config.save_camera_settings(key, settings)
            self._ip_apply_status.setStyleSheet(f"color:{_OK};font-size:10px;")
            self._ip_apply_status.setText(f"Guardado en {key}")
        except Exception as exc:
            self._ip_apply_status.setStyleSheet(f"color:{_NOK};font-size:10px;")
            self._ip_apply_status.setText(f"Error al guardar: {exc}")

    def _apply_ip_params(self) -> None:
        """EnvÃ­a parÃ¡metros de imagen a la cÃ¡mara vÃ­a VAPIX (Axis)."""
        params: dict = {}
        for pd in _IP_PARAM_DEFS:
            vapix_key = _IP_VAPIX_MAP.get(pd.key)
            if vapix_key:
                params[vapix_key] = self._ip_param_spinboxes[pd.key].value()
        if not params:
            return
        try:
            query = "&".join(
                ["action=update", "usergroup=admin"] + [f"{k}={v}" for k, v in params.items()]
            )
            ok, body = self._ip_param_request(query)
            if not ok:
                self._ip_apply_status.setStyleSheet(f"color:{_NOK};font-size:10px;")
                self._ip_apply_status.setText(f"VAPIX error: {body}")
                return

            verified_ok, verified = self._read_ip_camera_params()
            if verified_ok and isinstance(verified, dict):
                confirmed = []
                for pd in _IP_PARAM_DEFS:
                    current = verified.get(pd.key)
                    if current is None:
                        continue
                    current = max(pd.min_val, min(pd.max_val, current))
                    self._ip_param_spinboxes[pd.key].blockSignals(True)
                    self._ip_param_spinboxes[pd.key].setValue(current)
                    self._ip_param_spinboxes[pd.key].blockSignals(False)
                    confirmed.append(f"{pd.label}={current}")
                self._ip_apply_status.setStyleSheet(f"color:{_OK};font-size:10px;")
                self._ip_apply_status.setText("Aplicado y verificado: " + ", ".join(confirmed))
            else:
                self._ip_apply_status.setStyleSheet(f"color:{_WARN};font-size:10px;")
                self._ip_apply_status.setText(f"Aplicado sin verificacion: {body}")
        except Exception as exc:
            self._ip_apply_status.setStyleSheet(f"color:{_NOK};font-size:10px;")
            self._ip_apply_status.setText(f"VAPIX error: {exc}")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_scanner_changed(self, scanner_id: str) -> None:
        running = self._get_camera_running(scanner_id)
        self._cam_btn.setText("Detener cÃ¡mara" if running else "Iniciar cÃ¡mara")
        if running:
            self._preview_timer.start()
        else:
            self._preview_timer.stop()
            self._preview_lbl.setText("Sin seÃ±al de cÃ¡mara")

    def _toggle_camera(self) -> None:
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        cam = self._system.camera(scanner_id)
        if cam.is_running:
            cam.stop()
            self._preview_timer.stop()
            self._cam_btn.setText("Iniciar cÃ¡mara")
            self._preview_lbl.setText("Sin seÃ±al de cÃ¡mara")
        else:
            ok = cam.start()
            if ok:
                self._preview_timer.start()
                self._cam_btn.setText("Detener cÃ¡mara")
                self._status_lbl.setText("CÃ¡mara iniciada")
            else:
                self._status_lbl.setStyleSheet(f"color:{_NOK};font-size:11px;")
                self._status_lbl.setText("Error al abrir cÃ¡mara")

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
                dot = "â—" if ok else "â—‹"
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
            self._status_lbl.setText("CÃ¡mara no iniciada")
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
        self._status_lbl.setText("Valores leÃ­dos de la cÃ¡mara")

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
# Tab: SimulaciÃ³n de FSM de scanner
# ==================================================================

class ScannerSimTab(QWidget):
    """
    SimulaciÃ³n de ciclo completo AUTO sin cÃ¡mara real.

    Permite probar el recorrido:
      IDLE â†’ [Iniciar] â†’ RUNNING verde
           â†’ [Inyectar OK/NOK] â†’ luces amarilla/verde
           â†’ [1/3 NOK] â†’ streak parcial
           â†’ [Forzar FAULT] â†’ FAULT rojo parpadeante
           â†’ [Detener] â†’ STOPPED
           â†’ [Reset] â†’ IDLE azul
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

        title = QLabel("SimulaciÃ³n de ciclo de producciÃ³n â€” sin cÃ¡mara real")
        title.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        desc = QLabel(
            "Inicia el scanner en modo AUTO (solenoide + backlight ON, luces PLC activas) "
            "sin requerir cÃ¡mara. Usa los botones para simular resultados de inspecciÃ³n "
            "y verificar la FSM completa: IDLE â†’ RUNNING â†’ FAULT â†’ STOPPED â†’ IDLE."
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

            # â”€â”€ Estado actual â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

            thr_lbl = QLabel(f"Umbral FAULT: {threshold}  Â·  1/3 = {nok_third}")
            thr_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
            info_row.addWidget(thr_lbl)
            info_row.addStretch()
            grp_lay.addLayout(info_row)

            # â”€â”€ Botones de acciÃ³n â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

            b_start  = _btn("â–¶ Iniciar\n(sim AUTO)", "#166534")
            b_ok     = _btn("âœ“ Inyectar OK",         "#1e40af", 110)
            b_nok1   = _btn("âœ— Inyectar NOK\n(Ã—1)",  "#7f1d1d", 110)
            b_nok3   = _btn(f"âœ—âœ— {nok_third}Ã— NOK\n(1/3 umbral)", "#92400e", 120)
            b_fault  = _btn("âš¡ Forzar FAULT",        "#831843", 120)
            b_stop   = _btn("â–  Detener",              "#374151", 100)
            b_reset  = _btn("â†º Reset",                "#1e3a5f", 90)

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

            # â”€â”€ Secuencia sugerida â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            seq = QLabel(
                "Secuencia: Iniciar â†’ Inyectar OK â†’ 1/3 NOK â†’ Forzar FAULT â†’ Detener â†’ Reset"
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
    """Carga logo desde raÃ­z del proyecto escalado a max_h px de alto."""
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
        self.setWindowTitle("DEFYVISION â€” Modo Servicio")
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
        self._cam_tab    = CameraCalibTab(self._system)

        self._tabs.addTab(self._plc_tab,    "PLC I/O")
        self._tabs.addTab(self._diag_tab,   "DiagnÃ³stico HW")
        self._tabs.addTab(self._sys_tab,    "Sistema")
        self._tabs.addTab(self._log_tab,    "Logs")
        self._tabs.addTab(self._cfg_tab,    "ConfiguraciÃ³n")
        self._tabs.addTab(self._rec_tab,    "GrabaciÃ³n")
        self._tabs.addTab(self._cam_tab,    "CÃ¡mara")

        root.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
        """
        Header oscuro con logos reales â€” misma estructura que OperatorWindow.

        Layout de 3 secciones de ancho fijo igual (_HEADER_WING_W px c/u):
          [ala izquierda] | [centro: tÃ­tulo, stretch=1] | [ala derecha]
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

        # â”€â”€ Ala izquierda: logo Metalconf â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        left_wing = QWidget()
        left_wing.setFixedWidth(_HEADER_WING_W)
        left_wing.setStyleSheet("background:transparent;")
        left_lay = QHBoxLayout(left_wing)
        left_lay.setContentsMargins(0, 10, 0, 10)
        left_lay.setSpacing(0)
        left_lay.addWidget(_logo_label("logos/metalconf.png", 56))
        left_lay.addStretch()
        outer.addWidget(left_wing)

        # â”€â”€ Centro: tÃ­tulo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        subtitle = QLabel("Modo Servicio  Â·  DiagnÃ³stico")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color:#475569;font-size:10px;letter-spacing:1.5px;"
            "background:transparent;"
        )
        center_lay.addWidget(title)
        center_lay.addWidget(subtitle)
        outer.addWidget(center, stretch=1)

        # â”€â”€ Ala derecha: PLC badge + logo DEFYMOTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        self._header_plc = QLabel("â— PLC: â€”")
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
        logger.info(f"[Servicio] Reconectar PLC â†’ {'OK' if ok else 'FALLO'}")

    def _refresh(self) -> None:
        connected = self._system.plc.connected
        if connected != self._last_plc_connected:
            self._last_plc_connected = connected
            self._header_plc.setText(
                "â— PLC: Conectado" if connected else "â— PLC: Desconectado"
            )
            self._header_plc.setStyleSheet(
                f"color:{_OK if connected else _NOK};"
                "font-size:11px;font-weight:600;background:transparent;"
            )
        idx = self._tabs.currentIndex()
        if idx == 0:
            self._plc_tab.refresh()
        elif idx == 1:
            self._diag_tab.refresh()
        elif idx == 2:
            self._sys_tab.refresh()
        # LogsTab se actualiza por seÃ±al; ConfigTab es estÃ¡tico

    def closeEvent(self, event) -> None:
        self._timer.stop()
        logging.getLogger().removeHandler(self._log_handler)
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
