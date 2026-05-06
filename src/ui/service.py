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

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.utils.state import OperationMode, ScannerState

logger = logging.getLogger(__name__)

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
            lay.addLayout(self._sig_row(led, f"X{i}", self._x_name.get(i, "")))
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
            btn.setStyleSheet(
                "background:#374151;color:white;border-radius:4px;"
                "font-size:10px;font-weight:700;border:none;"
            )
            btn.clicked.connect(lambda _, idx=i: self._toggle(idx))
            self._y_btns.append(btn)
            lay.addLayout(self._sig_row(led, f"Y{i}", self._y_name.get(i, ""), btn))
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
                self._y_btns[i].setText("ON" if v else "OFF")
                self._y_btns[i].setStyleSheet(
                    f"background:{'#c2410c' if v else '#374151'};"
                    "color:white;border-radius:4px;font-size:10px;font-weight:700;border:none;"
                )

    def _toggle(self, idx: int) -> None:
        self._plc.write_coil(idx, not self._y_vals[idx])
        logger.info(f"[Diagnóstico] Toggle Y{idx} → {'ON' if not self._y_vals[idx] else 'OFF'}")


# ==================================================================
# Tab 2: Sistema
# ==================================================================

class SystemTab(QWidget):
    """Métricas de sesión por scanner y estado general del sistema."""

    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._scanner_labels: dict[str, dict[str, QLabel]] = {}
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
            grid = QGridLayout(group)
            grid.setSpacing(8)

            widgets: dict[str, QLabel] = {}
            for i, (key, label) in enumerate(_FIELDS):
                row, col = divmod(i, 3)
                w, lbl = self._kv(label, "—")
                grid.addWidget(w, row, col)
                widgets[key] = lbl

            lay.addWidget(group)
            self._scanner_labels[sid] = widgets

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
# Ventana de servicio
# ==================================================================

class ServiceWindow(QMainWindow):
    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self.setWindowTitle("DEFYVISION — Modo Servicio")
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

        self._plc_tab  = PLCIOTab(self._system)
        self._diag_tab = PLCDiagTab(self._system)
        self._sys_tab  = SystemTab(self._system)
        self._log_tab  = LogsTab(self._log_handler)
        self._cfg_tab  = ConfigTab()

        self._tabs.addTab(self._plc_tab,  "PLC I/O")
        self._tabs.addTab(self._diag_tab, "Diagnóstico HW")
        self._tabs.addTab(self._sys_tab,  "Sistema")
        self._tabs.addTab(self._log_tab,  "Logs")
        self._tabs.addTab(self._cfg_tab,  "Configuración")

        root.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {_DARK}, stop:1 #1e3a5f); border-radius:10px;"
        )
        lay = QHBoxLayout(header)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(14)

        logo = QLabel("DEFYMOTION")
        logo.setStyleSheet(
            f"color:{_ACCENT};font-size:16px;font-weight:700;"
            "letter-spacing:2px;background:transparent;"
        )
        lay.addWidget(logo)
        lay.addStretch()

        title = QLabel("DEFYVISION  ·  Modo Servicio")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color:{_TEXT};font-size:18px;font-weight:700;background:transparent;"
        )
        lay.addWidget(title)
        lay.addStretch()

        self._header_plc = QLabel("● PLC: —")
        self._header_plc.setStyleSheet(
            f"color:{_MUTED};font-size:11px;font-weight:600;background:transparent;"
        )
        lay.addWidget(self._header_plc)

        return header

    # ------------------------------------------------------------------

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
    app = QApplication.instance() or QApplication(sys.argv)
    win = ServiceWindow(system)
    win.show()
    app.exec()
