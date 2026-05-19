"""
Interfaz de servicio/calibración (PyQt6).

4 pestañas:
  PLC I/O       — tabla de señales en tiempo real, toggle de salidas
  Sistema       — métricas de sesión por scanner + estado PLC
  Logs          — visor de logs Python en tiempo real
  Configuración — visualización read-only de archivos YAML

Se lanza tras autenticación con LoginDialog.
Acepta un InspectionSystem existente (desde OperatorWindow) o crea uno propio.
"""

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap, QImage
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
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

# Ancho fijo de cada ala del header — igualar ambos lados centra el título
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
    """Reenvía registros al widget de logs mediante señal Qt (thread-safe)."""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _LogEmitter()
        self.signal   = self._emitter.record
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
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
        self._plc_status = QLabel("PLC: —")
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

            val_item = QTableWidgetItem("—")
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
        self._plc_status.setText("PLC: Conectado" if connected else "PLC: Desconectado")
        self._plc_status.setStyleSheet(
            f"color:{_OK if connected else _NOK};font-size:11px;font-weight:600;"
        )

        for name, (sig_type, _addr) in self._signals:
            item = self._value_items.get(name)
            if item is None:
                continue
            val = self._system.io.read(name)
            if val is None:
                item.setText("—")
                item.setForeground(QBrush(QColor(_MUTED)))
            else:
                item.setText("ON" if val else "OFF")
                if val:
                    color = _OK if sig_type == "output" else _ACCENT
                else:
                    color = _MUTED
                item.setForeground(QBrush(QColor(color)))

    def _toggle_output(self, name: str) -> None:
        current = self._system.io.read(name)
        new_val = not bool(current)
        self._system.io.write(name, new_val)
        logger.info(f"[Servicio] Toggle {name} → {'ON' if new_val else 'OFF'}")


# ==================================================================
# Tab: Diagnóstico HW — X0-X15 / Y0-Y15
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
                c = "#22c55e" if v else _BORDER
                self._x_leds[i].setStyleSheet(f"background:{c};border-radius:7px;")

        y_vals = self._plc.read_coils_batch(0, self._COUNT)
        if y_vals:
            for i, v in enumerate(y_vals):
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
        self._plc.write_coil(idx, not self._y_vals[idx])
        logger.info(f"[Diagnóstico] Toggle Y{idx} → {'ON' if not self._y_vals[idx] else 'OFF'}")


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

        title = QLabel("Prueba de salidas PLC — activar cada salida manualmente para verificar")
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
        logger.info(f"[PruebaS] {scanner_id}.{sig_key} → {'ON' if new_val else 'OFF'}")

    def _all_off(self, scanner_id: str) -> None:
        for sig_key in self._btns.get(scanner_id, {}):
            self._system.io.write(f"{scanner_id}.{sig_key}", False)
        logger.info(f"[PruebaS] {scanner_id} — Todo OFF")

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

        ip_w, self._plc_ip_lbl   = self._kv("IP / Puerto", "—")
        conn_w, self._plc_conn_lbl = self._kv("Estado",     "—")
        poll_w, self._plc_poll_lbl = self._kv("Poll interval", "—")
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
                w, lbl = self._kv(label, "—")
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

            start_btn = _btn("▶  Iniciar",  "#166534")
            stop_btn  = _btn("■  Detener",  "#7f1d1d")
            reset_btn = _btn("↺  Reset",    "#1e3a5f")
            sim_btn   = _btn("⚡  Forzar Inspección", "#78350f")
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

        self._plc_ip_lbl.setText(f"{plc_cfg['ip']}:{plc_cfg.get('port', 502)}")
        self._plc_conn_lbl.setText("Conectado" if connected else "Desconectado")
        self._plc_conn_lbl.setStyleSheet(
            f"font-size:12px;font-weight:700;"
            f"color:{_OK if connected else _NOK};"
        )
        self._plc_poll_lbl.setText(f"{plc_cfg.get('poll_interval_ms', 50)} ms")

        for sid, wdg in self._scanner_labels.items():
            s   = self._system.scanner(sid).get_status()
            cam = self._system.camera(sid)

            state: ScannerState       = s["state"]
            mode:  OperationMode      = s["mode"]
            start: Optional[datetime] = s.get("session_start")

            _state_colors = {
                ScannerState.IDLE:    _MUTED,
                ScannerState.RUNNING: _OK,
                ScannerState.FAULT:   _NOK,
                ScannerState.STOPPED: "#475569",
                ScannerState.ERROR:   _WARN,
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
                start.strftime("%H:%M:%S") if start else "—"
            )
            wdg["camera"].setText(
                f"#{cam.index} {'activa' if cam.is_running else 'inactiva'}"
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
                btns["cap"].setEnabled(self._system.camera(sid).is_running)

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
        try:
            from src.inspection import inspect_image
            results = []
            for i, p in enumerate(self._paths):
                results.append(inspect_image(self._model, p,
                                             scanner_id=self._scanner_id))
                self.progress.emit(i + 1, len(self._paths))
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


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

        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._grab_frame)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Control bar ───────────────────────────────────────────────
        ctrl = QGroupBox("Control de grabación")
        ctrl.setStyleSheet(self._grp_style())
        ctrl_lay = QHBoxLayout(ctrl)
        ctrl_lay.setSpacing(10)
        ctrl_lay.setContentsMargins(12, 14, 12, 10)

        lbl_sc = QLabel("Scanner:")
        lbl_sc.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        ctrl_lay.addWidget(lbl_sc)

        self._scanner_combo = QComboBox()
        self._scanner_combo.addItems(self._system.scanner_ids())
        self._scanner_combo.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 6px;font-size:11px;min-width:90px;"
        )
        ctrl_lay.addWidget(self._scanner_combo)

        lbl_model = QLabel("Modelo:")
        lbl_model.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        ctrl_lay.addWidget(lbl_model)

        self._model_combo = QComboBox()
        self._model_combo.addItems(DISPLAY_NAMES)
        self._model_combo.setStyleSheet(
            f"background:{_DARK};color:{_ACCENT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;min-width:130px;"
        )
        # Sync default selection with scanner's configured model
        default_sids = self._system.scanner_ids()
        if default_sids:
            _init_model = self._system.io.scanner_config(default_sids[0]).get("model", "")
            if _init_model:
                self._model_combo.setCurrentText(to_display(_init_model))
        ctrl_lay.addWidget(self._model_combo)

        self._scanner_combo.currentTextChanged.connect(self._on_scanner_changed)

        lbl_fps = QLabel("FPS captura:")
        lbl_fps.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        ctrl_lay.addWidget(lbl_fps)

        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(10)
        self._fps_spin.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 6px;font-size:11px;max-width:60px;"
        )
        ctrl_lay.addWidget(self._fps_spin)

        ctrl_lay.addSpacing(12)

        self._live_chk = QCheckBox("Análisis en vivo")
        self._live_chk.setChecked(False)
        self._live_chk.setStyleSheet(f"color:{_TEXT};font-size:11px;")
        ctrl_lay.addWidget(self._live_chk)

        ctrl_lay.addSpacing(8)

        self._btn_start = self._mk_btn("▶  Iniciar grabación", "#166534")
        self._btn_stop  = self._mk_btn("■  Detener",           "#7f1d1d")
        self._btn_stop.setEnabled(False)
        ctrl_lay.addWidget(self._btn_start)
        ctrl_lay.addWidget(self._btn_stop)

        ctrl_lay.addSpacing(16)

        self._frame_lbl = QLabel("0 frames")
        self._frame_lbl.setStyleSheet(
            f"color:{_ACCENT};font-size:13px;font-weight:700;"
        )
        ctrl_lay.addWidget(self._frame_lbl)
        ctrl_lay.addStretch()

        self._status_lbl = QLabel("Listo")
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        ctrl_lay.addWidget(self._status_lbl)

        root.addWidget(ctrl)

        # ── Camera panel ──────────────────────────────────────────────
        cam_grp = QGroupBox("Cámara")
        cam_grp.setStyleSheet(self._grp_style())
        cam_lay = QHBoxLayout(cam_grp)
        cam_lay.setSpacing(10)
        cam_lay.setContentsMargins(12, 14, 12, 10)

        self._btn_read_cam = self._mk_btn("↻  Leer cámara", "#374151")
        cam_lay.addWidget(self._btn_read_cam)

        cam_lay.addSpacing(16)

        self._cam_info_lbl = QLabel("—")
        self._cam_info_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;font-family:Consolas,monospace;"
        )
        cam_lay.addWidget(self._cam_info_lbl)

        cam_lay.addStretch()

        root.addWidget(cam_grp)

        # ── Analysis bar ──────────────────────────────────────────────
        ana = QGroupBox("Análisis")
        ana.setStyleSheet(self._grp_style())
        ana_lay = QHBoxLayout(ana)
        ana_lay.setSpacing(10)
        ana_lay.setContentsMargins(12, 14, 12, 10)

        self._btn_load = self._mk_btn("📂  Abrir grabación", "#374151")
        ana_lay.addWidget(self._btn_load)

        self._btn_analyze = self._mk_btn("⚙  Analizar grabación", "#0f4c81")
        self._btn_analyze.setEnabled(False)
        ana_lay.addWidget(self._btn_analyze)

        ana_lay.addSpacing(16)
        self._ana_progress = QLabel("")
        self._ana_progress.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        ana_lay.addWidget(self._ana_progress)

        ana_lay.addStretch()

        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet(f"color:{_MUTED};font-size:10px;")
        ana_lay.addWidget(self._stats_lbl)

        ana_lay.addSpacing(12)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color:{_TEXT};font-size:11px;font-weight:600;")
        ana_lay.addWidget(self._summary_lbl)

        root.addWidget(ana)

        # ── Browser ───────────────────────────────────────────────────
        browser = QGroupBox("Navegador de capturas")
        browser.setStyleSheet(self._grp_style())
        brow_lay = QVBoxLayout(browser)
        brow_lay.setContentsMargins(12, 14, 12, 10)
        brow_lay.setSpacing(8)

        # Nav buttons row
        nav_row = QHBoxLayout()
        self._btn_prev = self._mk_btn("◀  Anterior", "#334155")
        self._btn_next = self._mk_btn("Siguiente  ▶", "#334155")
        self._nav_lbl  = QLabel("—")
        self._nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_lbl.setStyleSheet(f"color:{_TEXT};font-size:11px;min-width:160px;")
        self._overlay_toggle = QPushButton("Ver overlay")
        self._overlay_toggle.setCheckable(True)
        self._overlay_toggle.setChecked(True)
        self._overlay_toggle.setFixedHeight(28)
        self._overlay_toggle.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:11px;padding:0 10px;border:none;"
            "checked: background:#1e3a5f;"
        )
        self._overlay_toggle.toggled.connect(lambda _: self._show_frame(self._current_idx))

        for w in (self._btn_prev, self._nav_lbl, self._btn_next, self._overlay_toggle):
            nav_row.addWidget(w)
        nav_row.addStretch()

        self._result_lbl = QLabel("")
        self._result_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._result_lbl.setStyleSheet(f"font-size:12px;font-weight:700;")
        nav_row.addWidget(self._result_lbl)
        brow_lay.addLayout(nav_row)

        # Image display
        self._img_label = QLabel("Sin frames")
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet(
            f"background:{_DARK};border:1px solid {_BORDER};border-radius:6px;"
            f"color:{_MUTED};font-size:13px;"
        )
        self._img_label.setMinimumHeight(360)
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        brow_lay.addWidget(self._img_label, stretch=1)

        root.addWidget(browser, stretch=1)

        # ── Signal wiring ─────────────────────────────────────────────
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_load.clicked.connect(self._on_load_recording)
        self._btn_analyze.clicked.connect(self._on_analyze)
        self._btn_read_cam.clicked.connect(self._refresh_cam_info)
        self._btn_prev.clicked.connect(lambda: self._show_frame(self._current_idx - 1))
        self._btn_next.clicked.connect(lambda: self._show_frame(self._current_idx + 1))

        self._update_nav_state()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        sid = self._scanner_combo.currentText()
        cam = self._system.camera(sid)
        if not cam.is_running:
            self._status_lbl.setText("La cámara no está activa")
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
        self._img_label.setText("Sin frames")
        self._update_nav_state()

        # Persist metadata alongside the frames so we can reload this recording later
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

        interval_ms = max(16, 1000 // self._fps_spin.value())
        self._rec_timer.start(interval_ms)
        self._recording = True

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_analyze.setEnabled(False)
        self._btn_load.setEnabled(False)
        self._scanner_combo.setEnabled(False)
        self._model_combo.setEnabled(False)
        self._fps_spin.setEnabled(False)
        self._live_chk.setEnabled(False)
        mode_txt = " (en vivo)" if self._live_chk.isChecked() else ""
        self._status_lbl.setText(f"Grabando{mode_txt} → {self._rec_dir.name}")
        logger.info(f"[Grabación] inicio en {self._rec_dir}  modelo={meta['model_display']}  fps={meta['fps']}")

    def _on_stop(self) -> None:
        self._rec_timer.stop()
        self._recording = False
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_load.setEnabled(True)
        self._scanner_combo.setEnabled(True)
        self._model_combo.setEnabled(True)
        self._fps_spin.setEnabled(True)
        self._live_chk.setEnabled(True)
        n = len(self._frame_paths)
        self._status_lbl.setText(f"Detenido — {n} frames en {self._rec_dir.name}")
        # If live analysis was active, results are already populated; show analyze button
        # to allow re-analysis if desired (e.g. after changing tolerances).
        self._btn_analyze.setEnabled(n > 0)
        if n > 0 and not self._results:
            self._show_frame(0)
        logger.info(f"[Grabación] detenida — {n} frames")

    def _grab_frame(self) -> None:
        import cv2
        sid = self._scanner_combo.currentText()
        cam = self._system.camera(sid)
        frame = cam.get_frame()
        if frame is None:
            return
        idx = len(self._frame_paths)
        path = self._rec_dir / f"frame_{idx:04d}.png"
        cv2.imwrite(str(path), frame)
        self._frame_paths.append(path)
        self._frame_lbl.setText(f"{idx + 1} frames")

        if self._live_chk.isChecked():
            try:
                from src.inspection import inspect_image
                result = inspect_image(self._active_model(), path,
                                      scanner_id=self._scanner_combo.currentText() or None)
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

    def _on_analyze(self) -> None:
        if not self._frame_paths:
            return
        self._btn_analyze.setEnabled(False)
        self._results.clear()
        self._stats_lbl.setText("")
        self._ana_progress.setText("Analizando…")

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
        self._ana_progress.setText(f"Analizando {done}/{total}…")

    def _on_ana_done(self, results: list) -> None:
        self._results = results
        ok  = sum(1 for r in results if r.status == "OK")
        nok = len(results) - ok
        pct = round(100 * ok / len(results)) if results else 0

        # Temporal decision (consecutive NOK rule)
        from src.inspection import _apply_temporal_rule
        from src.utils.config import load_tolerances
        model = self._active_model()
        tols = load_tolerances(model)
        consec = int(tols.get("consecutive_nok_frames", 5))
        temporal = _apply_temporal_rule(results, consec)
        t_ok  = sum(1 for t in temporal if t.decision_status == "OK")
        t_nok = len(temporal) - t_ok
        t_pct = round(100 * t_ok / len(temporal)) if temporal else 0
        self._summary_lbl.setText(
            f"Frame OK: {ok} ({pct}%)  NOK: {nok}  Total: {len(results)}"
            f"    |    Temporal OK: {t_ok} ({t_pct}%)  NOK: {t_nok}"
            f"  [umbral={consec}]"
        )

        if results:
            missing_counts = [len(r.report.missing_points) for r in results]
            avg_m = sum(missing_counts) / len(missing_counts)
            min_m, max_m = min(missing_counts), max(missing_counts)
            shifts = [r.shift_xy for r in results if r.shift_xy is not None]
            if shifts:
                import math
                shift_mags = [math.hypot(s[0], s[1]) for s in shifts]
                avg_shift = sum(shift_mags) / len(shift_mags)
                self._stats_lbl.setText(
                    f"missing avg={avg_m:.1f}  min={min_m}  max={max_m}"
                    f"    shift avg={avg_shift:.1f}px  max={max(shift_mags):.1f}px"
                )
            else:
                self._stats_lbl.setText(
                    f"missing avg={avg_m:.1f}  min={min_m}  max={max_m}"
                )

        self._ana_progress.setText("Análisis completo")
        self._btn_analyze.setEnabled(True)
        self._show_frame(0)
        logger.info(f"[Grabación] análisis completo — OK={ok} NOK={nok}")

    def _on_ana_error(self, msg: str) -> None:
        self._ana_progress.setText(f"Error: {msg}")
        self._btn_analyze.setEnabled(True)
        logger.error(f"[Grabación] error de análisis: {msg}")

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _show_frame(self, idx: int) -> None:
        if not self._frame_paths:
            return
        idx = max(0, min(idx, len(self._frame_paths) - 1))
        self._current_idx = idx

        show_overlay = self._overlay_toggle.isChecked() and idx < len(self._results)
        if show_overlay:
            import numpy as np
            bgr = self._results[idx].overlay
        else:
            import cv2, numpy as np
            bgr = cv2.imread(str(self._frame_paths[idx]))

        if bgr is not None:
            rgb   = bgr[:, :, ::-1].copy()
            h, w  = rgb.shape[:2]
            qi    = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
            px    = QPixmap.fromImage(qi)
            avail = self._img_label.size()
            self._img_label.setPixmap(
                px.scaled(avail, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )

        total = len(self._frame_paths)
        self._nav_lbl.setText(f"Frame {idx + 1} / {total}")

        if idx < len(self._results):
            r = self._results[idx]
            color = _OK if r.status == "OK" else _NOK
            missing = len(r.report.missing_points)
            self._result_lbl.setText(f"{r.status}  missing={missing}")
            self._result_lbl.setStyleSheet(f"font-size:12px;font-weight:700;color:{color};")
        else:
            self._result_lbl.setText("")

        self._update_nav_state()

    def _update_nav_state(self) -> None:
        n = len(self._frame_paths)
        self._btn_prev.setEnabled(self._current_idx > 0)
        self._btn_next.setEnabled(self._current_idx < n - 1)

    # ------------------------------------------------------------------

    def _mk_btn(self, text: str, bg: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(30)
        b.setStyleSheet(
            f"background:{bg};color:white;border-radius:5px;"
            "font-size:11px;font-weight:700;border:none;padding:0 12px;"
        )
        return b

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        for label, key in _READ:
            v = cam.read_setting(key)
            parts.append(f"{label}:{v:.0f}" if v >= 0 else f"{label}:?")
        fps_real = cam.fps
        parts.append(f"real:{fps_real:.1f}" if fps_real > 0 else "real:—")
        self._cam_info_lbl.setText("  ".join(parts))

    def _active_model(self) -> str:
        """Return the internal model ID currently selected in the model combo."""
        return to_internal(self._model_combo.currentText())

    def _on_scanner_changed(self, sid: str) -> None:
        """When scanner selection changes, sync model combo to that scanner's model."""
        if self._recording:
            return
        model_internal = self._system.io.scanner_config(sid).get("model", "")
        if model_internal:
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentText(to_display(model_internal))
            self._model_combo.blockSignals(False)

    def _on_load_recording(self) -> None:
        """Open an existing recording folder and load its frames for analysis."""
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

        self._rec_dir = folder_path
        self._frame_paths = frames
        self._results.clear()
        self._current_idx = 0
        self._summary_lbl.setText("")
        self._stats_lbl.setText("")
        self._ana_progress.setText("")

        # Load meta.json if present and sync model combo
        meta_path = folder_path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                model_display = meta.get("model_display", "")
                if model_display:
                    self._model_combo.setCurrentText(model_display)
                fps_saved = meta.get("fps")
                if fps_saved:
                    self._fps_spin.setValue(fps_saved)
                logger.info(f"[Grabación] meta cargada: {meta}")
            except Exception as exc:
                logger.warning(f"[Grabación] no se pudo leer meta.json: {exc}")

        self._btn_analyze.setEnabled(True)
        self._show_frame(0)
        self._status_lbl.setText(f"Cargado — {len(frames)} frames de {folder_path.name}")
        logger.info(f"[Grabación] cargada carpeta {folder_path.name} con {len(frames)} frames")

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
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

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.setInterval(200)

        self._build_ui()
        self._populate_scanner_selector()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

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

        self._status_lbl = QLabel("—")
        self._status_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(self._status_lbl)

        root.addLayout(top)

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
        root.addLayout(main, stretch=1)

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

        # Slider (mapped 0 → max-min)
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

        # Start with auto enabled → manual controls disabled
        if pd.auto_key:
            slider.setEnabled(False)
            sb.setEnabled(False)

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
                dot = "●" if ok else "○"
                self._status_lbl.setStyleSheet(
                    f"color:{'#4ade80' if ok else '#fbbf24'};font-size:11px;"
                )
                self._status_lbl.setText(f"{dot} {key}={value:.0f}")
        except KeyError:
            pass

    def _update_preview(self) -> None:
        scanner_id = self._scanner_combo.currentText()
        if not scanner_id:
            return
        try:
            cam = self._system.camera(scanner_id)
        except KeyError:
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
                       Qt.TransformationMode.SmoothTransformation)
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
      IDLE → [Iniciar] → RUNNING verde
           → [Inyectar OK/NOK] → luces amarilla/verde
           → [1/3 NOK] → streak parcial
           → [Forzar FAULT] → FAULT rojo parpadeante
           → [Detener] → STOPPED
           → [Reset] → IDLE azul
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

        title = QLabel("Simulación de ciclo de producción — sin cámara real")
        title.setStyleSheet(f"color:{_ACCENT};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        desc = QLabel(
            "Inicia el scanner en modo AUTO (solenoide + backlight ON, luces PLC activas) "
            "sin requerir cámara. Usa los botones para simular resultados de inspección "
            "y verificar la FSM completa: IDLE → RUNNING → FAULT → STOPPED → IDLE."
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

            thr_lbl = QLabel(f"Umbral FAULT: {threshold}  ·  1/3 = {nok_third}")
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

            b_start  = _btn("▶ Iniciar\n(sim AUTO)", "#166534")
            b_ok     = _btn("✓ Inyectar OK",         "#1e40af", 110)
            b_nok1   = _btn("✗ Inyectar NOK\n(×1)",  "#7f1d1d", 110)
            b_nok3   = _btn(f"✗✗ {nok_third}× NOK\n(1/3 umbral)", "#92400e", 120)
            b_fault  = _btn("⚡ Forzar FAULT",        "#831843", 120)
            b_stop   = _btn("■ Detener",              "#374151", 100)
            b_reset  = _btn("↺ Reset",                "#1e3a5f", 90)

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
                "Secuencia: Iniciar → Inyectar OK → 1/3 NOK → Forzar FAULT → Detener → Reset"
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
        self.setWindowTitle("DEFYVISION — Modo Servicio")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.resize(1200, 760)

        self._log_handler = QtLogHandler()
        logging.getLogger().addHandler(self._log_handler)

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
        self._tabs.addTab(self._diag_tab,   "Diagnóstico HW")
        self._tabs.addTab(self._sys_tab,    "Sistema")
        self._tabs.addTab(self._log_tab,    "Logs")
        self._tabs.addTab(self._cfg_tab,    "Configuración")
        self._tabs.addTab(self._rec_tab,    "Grabación")
        self._tabs.addTab(self._cam_tab,    "Cámara")

        root.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
        """
        Header oscuro con logos reales — misma estructura que OperatorWindow.

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

        self._header_plc = QLabel("● PLC: —")
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
        logger.info(f"[Servicio] Reconectar PLC → {'OK' if ok else 'FALLO'}")

    def _refresh(self) -> None:
        connected = self._system.plc.connected
        self._header_plc.setText(
            "● PLC: Conectado" if connected else "● PLC: Desconectado"
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
        # LogsTab se actualiza por señal; ConfigTab es estático

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
