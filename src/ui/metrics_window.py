"""
Ventana de métricas del sistema DEFYVISION.

Dos pestañas:
  Tiempo Real  — tarjetas con valores actualizados cada segundo
  Historial    — gráficos de tendencia consultados desde SQLite (matplotlib)

Separación clara de roles:
  UI Operador  → producción, limpia
  UI Servicio  → herramientas técnicas
  UI Métricas  → análisis y diagnóstico del sistema
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    _MPL = True
except ImportError:
    _MPL = False
    logger.warning("matplotlib no disponible — pestaña Historial deshabilitada")

# ------------------------------------------------------------------
# Paleta (dark, igual que service.py)
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

_HEADER_WING_W = 310

# ------------------------------------------------------------------
# Definición de métricas de tiempo real
# (id, etiqueta, unidad, tooltip para el operador)
# ------------------------------------------------------------------
_METRICS_DEF = [
    ("inspection_uptime_pct", "Uptime inspección", "%",
     "Porcentaje del tiempo de sesión en que el scanner estuvo realmente\n"
     "disponible para inspeccionar, descontando períodos sin cámara."),

    ("ok_pct",             "Calidad OK",        "%",
     "Porcentaje de inspecciones sin defectos sobre el total de la sesión.\n"
     "Meta de referencia: 95 % o más."),

    ("nok_pct",            "Defectos NOK",       "%",
     "Porcentaje de piezas con agujeros faltantes detectados.\n"
     "Cualquier valor por encima de 5 % merece atención."),

    ("nok_streak",         "Racha NOK actual",   "",
     "Cantidad de inspecciones NOK consecutivas en este momento.\n"
     "Cuando alcanza el límite configurado, el sistema entra en FAULT."),

    ("max_nok_streak",     "Racha NOK máx.",     "",
     "Peor racha de NOK consecutivos registrada desde que inició la sesión.\n"
     "Útil para detectar problemas intermitentes."),

    ("avg_missing_holes",  "Faltantes prom.",    "huecos",
     "Promedio de agujeros no detectados por inspección NOK.\n"
     "Un número alto indica que el defecto es grave o sistemático."),

    ("insp_per_min",       "Ritmo",              "/min",
     "Inspecciones por minuto. Refleja la velocidad de avance de la lámina.\n"
     "Depende de 'continuous_position_threshold' en tolerancias.yaml."),

    ("session_duration",   "Sesión activa",      "",
     "Tiempo transcurrido desde que se inició el scanner (hh:mm:ss)."),

    ("fault_count",        "Paradas FAULT",      "",
     "Cantidad de veces que el sistema detuvo la producción por racha NOK\n"
     "excesiva en esta sesión. Cero es lo ideal."),

    ("machine_stop_count", "Stops máquina",      "",
     "Cantidad de machine_stop disparados por lógica de defecto persistente o\n"
     "desalineación crítica detectada por el sistema."),

    ("camera_fps",         "FPS cámara",         "fps",
     "Velocidad de captura real de la cámara.\n"
     "Un valor bajo puede indicar problema de hardware o conexión USB."),

    ("camera_missing_events", "Cortes cámara",   "",
     "Cantidad de veces que el sistema perdió la señal de cámara durante esta sesión."),

    ("camera_missing_sec", "Sin cámara",         "s",
     "Tiempo acumulado de sesión sin frames válidos de cámara."),

    ("low_quality_pct",    "Baja calidad",       "%",
     "Porcentaje de inspecciones que quedaron en LOW_QUALITY y no se usaron\n"
     "plenamente para la decisión temporal."),

    ("avg_detection_ratio","Detección prom.",    "%",
     "Promedio de agujeros detectados respecto del patrón esperado. Útil para\n"
     "evaluar si el sistema está trabajando cómodo o al límite."),

    ("align_fail_count",   "Align fallback",     "",
     "Cantidad de inspecciones donde falló la alineación principal y se usó\n"
     "camino de fallback. Un valor alto merece revisión."),

    ("last_position_diff", "Desplaz. lámina",    "px",
     "Diferencia media de píxeles entre el frame actual y el último\n"
     "inspeccionado. Refleja la velocidad de avance de la lámina."),
]

# Métricas que se grafican en la pestaña Historial
# (columna_db, etiqueta, color, y_label)
_CHART_DEF = [
    ("ok_pct",       "Calidad OK (%)",      _OK,     "%"),
    ("insp_per_min", "Inspecciones / min",  _ACCENT, "insp/min"),
    ("nok_streak",   "Racha NOK",           _NOK,    "consecutivos"),
    ("camera_fps",   "FPS cámara",          _WARN,   "fps"),
    ("avg_detection_ratio", "Detección prom. (%)", "#a78bfa", "%"),
    ("low_quality_pct", "Baja calidad (%)", "#f97316", "%"),
]

_TIME_RANGES = [
    ("1 h",  1),
    ("4 h",  4),
    ("8 h",  8),
    ("24 h", 24),
    ("48 h", 48),
]


# ==================================================================
# Widget: tarjeta de una sola métrica
# ==================================================================

class _MetricCard(QWidget):
    """Tarjeta compacta: título + valor grande + unidad + tooltip."""

    def __init__(self, label: str, unit: str, tooltip: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setStyleSheet(
            f"QWidget {{ background:{_PANEL};border-radius:8px;"
            f"border:1px solid {_BORDER}; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"font-size:8px;color:{_MUTED};letter-spacing:0.5px;background:transparent;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._val = QLabel("—")
        self._val.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setStyleSheet(f"color:{_MUTED};background:transparent;")

        lay.addWidget(lbl)
        lay.addWidget(self._val)
        if unit:
            u = QLabel(unit)
            u.setStyleSheet(
                f"font-size:8px;color:{_MUTED};background:transparent;"
            )
            u.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(u)

    def update(self, text: str, color: str = _TEXT) -> None:
        self._val.setText(text)
        self._val.setStyleSheet(
            f"color:{color};background:transparent;"
            "font-size:17px;font-weight:700;"
        )


# ==================================================================
# Pestaña: Tiempo Real
# ==================================================================

class _RealTimeTab(QWidget):
    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self._cards: dict[str, dict[str, _MetricCard]] = {}
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{_DARK}; }}")

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        for sid in self._system.scanner_ids():
            num = sid.split("_")[-1]
            grp = QGroupBox(f"SCANNER {num}   ·   BAL {num}")
            grp.setStyleSheet(self._grp_style())
            grid = QGridLayout(grp)
            grid.setContentsMargins(10, 16, 10, 10)
            grid.setSpacing(8)

            cards: dict[str, _MetricCard] = {}
            for i, (mid, label, unit, tip) in enumerate(_METRICS_DEF):
                card = _MetricCard(label, unit, tip)
                card.setMinimumWidth(110)
                row, col = divmod(i, 4)
                grid.addWidget(card, row, col)
                cards[mid] = card

            lay.addWidget(grp)
            self._cards[sid] = cards

        lay.addStretch()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _refresh(self) -> None:
        for sid, cards in self._cards.items():
            try:
                s   = self._system.scanner(sid).get_status()
                cam = self._system.camera(sid)
            except KeyError:
                continue

            total  = s.get("total_inspections", 0)
            ok     = s.get("ok_count", 0)
            nok    = s.get("nok_count", 0)
            ok_pct  = ok  / total * 100 if total > 0 else 0.0
            nok_pct = nok / total * 100 if total > 0 else 0.0
            streak  = s.get("nok_streak", 0)
            max_str = s.get("max_nok_streak", 0)
            fault_c = s.get("fault_count", 0)
            machine_stop_c = s.get("machine_stop_count", 0)
            avg_mis = s.get("avg_missing_holes", 0.0)
            avg_det_ratio = s.get("avg_detection_ratio", 0.0) * 100.0
            align_fail_c = s.get("align_fail_count", 0)
            low_quality_pct = s.get("low_quality_pct", 0.0)
            camera_missing_sec = s.get("camera_missing_sec", 0.0)
            camera_missing_events = s.get("camera_missing_events", 0)
            insp_uptime_pct = s.get("inspection_uptime_pct", 0.0)
            pos_dif = s.get("last_position_diff", 0.0)
            fps     = cam.fps

            start = s.get("session_start")
            if start:
                dur = (datetime.now() - start).total_seconds()
                ipm = total / dur * 60 if dur > 0 else 0.0
                hms = str(timedelta(seconds=int(dur)))
            else:
                dur = ipm = 0.0
                hms = "—"

            updates = {
                "inspection_uptime_pct": (
                    f"{insp_uptime_pct:.1f}",
                    _OK if insp_uptime_pct >= 98 else (_WARN if insp_uptime_pct >= 90 else _NOK),
                ),
                "ok_pct": (
                    f"{ok_pct:.1f}",
                    _OK if ok_pct >= 95 else (_WARN if ok_pct >= 80 else _NOK),
                ),
                "nok_pct": (
                    f"{nok_pct:.1f}",
                    _NOK if nok_pct > 5 else (_WARN if nok_pct > 0 else _MUTED),
                ),
                "nok_streak": (
                    str(streak),
                    _NOK if streak > 0 else _MUTED,
                ),
                "max_nok_streak": (
                    str(max_str),
                    _NOK if max_str > 0 else _MUTED,
                ),
                "avg_missing_holes": (
                    f"{avg_mis:.1f}",
                    _WARN if avg_mis > 0 else _MUTED,
                ),
                "insp_per_min": (
                    f"{ipm:.1f}",
                    _ACCENT if ipm > 0 else _MUTED,
                ),
                "session_duration": (hms, _TEXT),
                "fault_count": (
                    str(fault_c),
                    _NOK if fault_c > 0 else _MUTED,
                ),
                "machine_stop_count": (
                    str(machine_stop_c),
                    _NOK if machine_stop_c > 0 else _MUTED,
                ),
                "camera_fps": (
                    f"{fps:.1f}",
                    _OK if fps >= 20 else (_WARN if fps >= 5 else _NOK),
                ),
                "camera_missing_events": (
                    str(camera_missing_events),
                    _NOK if camera_missing_events > 0 else _MUTED,
                ),
                "camera_missing_sec": (
                    f"{camera_missing_sec:.1f}",
                    _NOK if camera_missing_sec > 10 else (_WARN if camera_missing_sec > 0 else _MUTED),
                ),
                "low_quality_pct": (
                    f"{low_quality_pct:.1f}",
                    _NOK if low_quality_pct > 15 else (_WARN if low_quality_pct > 5 else _OK),
                ),
                "avg_detection_ratio": (
                    f"{avg_det_ratio:.1f}",
                    _OK if avg_det_ratio >= 95 else (_WARN if avg_det_ratio >= 80 else _NOK),
                ),
                "align_fail_count": (
                    str(align_fail_c),
                    _NOK if align_fail_c > 0 else _MUTED,
                ),
                "last_position_diff": (
                    f"{pos_dif:.1f}",
                    _ACCENT if pos_dif > 0 else _MUTED,
                ),
            }
            for mid, (val, color) in updates.items():
                if mid in cards:
                    cards[mid].update(val, color)

    def _grp_style(self) -> str:
        return (
            f"QGroupBox {{ background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;margin-top:12px;padding-top:10px;"
            f"font-size:12px;font-weight:700;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:12px;padding:0 4px; }}"
        )

    def stop(self) -> None:
        self._timer.stop()


# ==================================================================
# Pestaña: Historial
# ==================================================================

class _HistoricalTab(QWidget):
    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system  = system
        self._hours   = 8
        self._scanner = system.scanner_ids()[0] if system.scanner_ids() else ""
        self._range_btns: list[QPushButton] = []
        self._axes:   list = []
        self._canvas  = None
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── barra superior ────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(10)

        sc_lbl = QLabel("Scanner:")
        sc_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(sc_lbl)

        self._sc_combo = QComboBox()
        self._sc_combo.addItems(self._system.scanner_ids())
        self._sc_combo.setStyleSheet(
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:4px;padding:2px 8px;font-size:11px;"
        )
        self._sc_combo.currentTextChanged.connect(self._on_scanner_changed)
        top.addWidget(self._sc_combo)

        top.addSpacing(16)
        rng_lbl = QLabel("Rango:")
        rng_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        top.addWidget(rng_lbl)

        for label, hours in _TIME_RANGES:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(self._range_style(hours == self._hours))
            btn.clicked.connect(
                lambda _, h=hours, b=btn: self._on_range(h, b)
            )
            top.addWidget(btn)
            self._range_btns.append(btn)

        top.addStretch()

        ref_btn = QPushButton("Actualizar")
        ref_btn.setFixedHeight(28)
        ref_btn.setStyleSheet(
            f"background:{_ACCENT};color:#000;font-weight:700;"
            "border-radius:5px;padding:0 16px;border:none;font-size:11px;"
        )
        ref_btn.clicked.connect(self._update_charts)
        top.addWidget(ref_btn)
        root.addLayout(top)

        # ── área de gráficos ──────────────────────────────────────
        if _MPL:
            self._fig = Figure(figsize=(12, 6), facecolor=_DARK)
            self._axes = []
            n_plots = len(_CHART_DEF)
            for i in range(n_plots):
                ax = self._fig.add_subplot(3, 2, i + 1)
                self._axes.append(ax)
            self._fig.tight_layout(pad=2.5)

            self._canvas = FigureCanvas(self._fig)
            self._canvas.setStyleSheet(f"background:{_DARK};")
            root.addWidget(self._canvas, stretch=1)
            self._update_charts()
        else:
            msg = QLabel(
                "matplotlib no está instalado.\n"
                "Instalar con:  pip install matplotlib"
            )
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet(
                f"color:{_WARN};font-size:13px;background:{_DARK};"
            )
            root.addWidget(msg, stretch=1)

    def _range_style(self, active: bool) -> str:
        bg = _ACCENT if active else _PANEL
        fg = "#000000" if active else _TEXT
        return (
            f"background:{bg};color:{fg};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:11px;padding:0 10px;"
            "font-weight:700;"
        )

    def _on_scanner_changed(self, sid: str) -> None:
        self._scanner = sid
        self._update_charts()

    def _on_range(self, hours: int, clicked_btn: QPushButton) -> None:
        self._hours = hours
        for btn in self._range_btns:
            label = btn.text()
            h = next((h for lbl, h in _TIME_RANGES if lbl == label), None)
            btn.setStyleSheet(self._range_style(h == hours))
        self._update_charts()

    def _update_charts(self) -> None:
        if not _MPL or self._canvas is None or not self._scanner:
            return

        rows = self._system.metrics.query(self._scanner, self._hours)

        for ax, (col, lbl, color, ylabel) in zip(self._axes, _CHART_DEF):
            ax.cla()
            ax.set_facecolor(_PANEL)
            ax.set_title(lbl, fontsize=9, pad=5, color=_TEXT)
            ax.set_ylabel(ylabel, fontsize=8, color=_MUTED)
            ax.tick_params(colors=_MUTED, labelsize=7)
            for spine in ax.spines.values():
                spine.set_color(_BORDER)
            ax.grid(True, color=_BORDER, linewidth=0.4, linestyle="--", alpha=0.6)

            if not rows:
                ax.text(
                    0.5, 0.5, "Sin datos aún\n(primeros datos en ~1 min)",
                    transform=ax.transAxes,
                    ha="center", va="center",
                    color=_MUTED, fontsize=9,
                )
                continue

            xs = [datetime.fromtimestamp(r["ts"]) for r in rows]
            ys = [r.get(col) or 0.0 for r in rows]

            ax.plot(xs, ys, color=color, linewidth=1.5,
                    marker=".", markersize=3, zorder=3)
            ax.fill_between(xs, ys, alpha=0.10, color=color)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            self._fig.autofmt_xdate(rotation=30, ha="right")

            if col == "ok_pct":
                ax.axhline(
                    y=95, color=_OK, linewidth=0.8,
                    linestyle="--", alpha=0.5,
                )
                ax.set_ylim(-2, 102)
            elif ys:
                ax.set_ylim(bottom=0)

        self._fig.tight_layout(pad=2.5)
        self._canvas.draw()


# ==================================================================
# Ventana principal
# ==================================================================

class MetricsWindow(QMainWindow):
    def __init__(self, system: InspectionSystem, parent=None) -> None:
        super().__init__(parent)
        self._system = system
        self.setWindowTitle("DEFYVISION — Métricas")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.resize(1150, 720)
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background:{_DARK};")
        self.setCentralWidget(central)

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
                padding:8px 22px;font-size:12px;border-radius:5px;margin-right:3px;
            }}
            QTabBar::tab:selected {{
                background:{_PANEL};color:{_TEXT};font-weight:700;
            }}
        """)

        self._rt_tab   = _RealTimeTab(self._system)
        self._hist_tab = _HistoricalTab(self._system)

        self._tabs.addTab(self._rt_tab,   "Tiempo Real")
        self._tabs.addTab(self._hist_tab, "Historial")

        root.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
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

        # Ala izquierda
        lw = QWidget()
        lw.setFixedWidth(_HEADER_WING_W)
        lw.setStyleSheet("background:transparent;")
        ll = QHBoxLayout(lw)
        ll.setContentsMargins(0, 10, 0, 10)
        ll.addWidget(_logo_label("logos/metalconf.png", 56))
        ll.addStretch()
        outer.addWidget(lw)

        # Centro
        center = QWidget()
        center.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(3)
        cl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        t = QLabel("DEFYVISION")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(
            "color:#f1f5f9;font-size:26px;font-weight:700;"
            "letter-spacing:4px;background:transparent;"
        )
        s = QLabel("Panel de Métricas  ·  Diagnóstico")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet(
            "color:#475569;font-size:10px;letter-spacing:1.5px;"
            "background:transparent;"
        )
        cl.addWidget(t)
        cl.addWidget(s)
        outer.addWidget(center, stretch=1)

        # Ala derecha
        rw = QWidget()
        rw.setFixedWidth(_HEADER_WING_W)
        rw.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addStretch()
        dm = _logo_label("logos/defymotion.jpg", 48)
        dm.setContentsMargins(6, 0, 0, 0)
        rl.addWidget(dm)
        outer.addWidget(rw)

        return header

    def closeEvent(self, event) -> None:
        self._rt_tab.stop()
        event.accept()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _logo_label(rel_path: str, max_h: int) -> QLabel:
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
