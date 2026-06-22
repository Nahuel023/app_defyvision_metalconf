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
    QFrame,
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

from src.utils.paths import app_root
_ROOT = app_root()

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
_SURFACE = "#111827"
_SURFACE_2 = "#172033"
_CHIP = "#243247"
_HEADER_LINE = "#67e8f9"

_HEADER_WING_W = 310

# ------------------------------------------------------------------
# Definición de métricas de tiempo real
# (id, etiqueta, unidad, tooltip para el operador)
# Lenguaje simple a propósito: esta pantalla la lee el operario en planta,
# no un técnico. Sin siglas ni palabras en inglés (uptime, fps, ratio, etc.).
# ------------------------------------------------------------------
_METRICS_DEF = [
    ("inspection_uptime_pct", "Tiempo trabajando", "%",
     "De todo el tiempo que el scanner estuvo encendido, qué porcentaje\n"
     "pudo usarse de verdad para inspeccionar (sin contar los cortes de cámara)."),

    ("ok_pct",             "Piezas buenas",     "%",
     "De cada 100 piezas inspeccionadas, cuántas salieron sin defectos.\n"
     "Cuanto más alto, mejor. Lo ideal es 95 % o más."),

    ("nok_pct",            "Piezas con falla",   "%",
     "De cada 100 piezas inspeccionadas, cuántas tenían agujeros faltantes.\n"
     "Si pasa de 5 %, conviene avisar y revisar la máquina."),

    ("nok_streak",         "Fallas seguidas ahora", "",
     "Cuántas piezas con falla salieron una detrás de otra, justo ahora.\n"
     "Si llega a un número límite, la máquina se detiene sola."),

    ("max_nok_streak",     "Peor racha de fallas", "",
     "La mayor cantidad de fallas seguidas que hubo desde que se inició\n"
     "esta sesión, aunque ya se haya solucionado."),

    ("avg_missing_holes",  "Agujeros que faltan (promedio)", "agujeros",
     "En las piezas con falla, cuántos agujeros faltaron en promedio.\n"
     "Un número alto indica un defecto más grave."),

    ("insp_per_min",       "Piezas por minuto", "/min",
     "Cuántas piezas se están inspeccionando por minuto.\n"
     "Refleja qué tan rápido está avanzando la línea."),

    ("session_duration",   "Tiempo encendido",   "",
     "Cuánto tiempo pasó desde que se inició este scanner (hh:mm:ss)."),

    ("fault_count",        "Veces que se detuvo sola", "",
     "Cuántas veces la máquina se frenó por demasiadas fallas seguidas\n"
     "en esta sesión. Lo ideal es cero."),

    ("machine_stop_count", "Paradas por defecto repetido", "",
     "Cuántas veces se detuvo la máquina porque el mismo defecto se repitió\n"
     "varias veces, o porque la chapa se vio mal alineada o torcida."),

    ("camera_fps",         "Velocidad de la cámara", "img/seg",
     "Cuántas imágenes por segundo está captando la cámara.\n"
     "Si baja mucho, puede haber un problema de cable o de la cámara."),

    ("camera_missing_events", "Cortes de cámara", "",
     "Cuántas veces se perdió la imagen de la cámara durante esta sesión."),

    ("camera_missing_sec", "Tiempo sin cámara",  "seg",
     "Cuánto tiempo en total estuvo la cámara sin dar imagen."),

    ("low_quality_pct",    "Imágenes borrosas",  "%",
     "Porcentaje de fotos que salieron borrosas o movidas y no se\n"
     "pudieron usar bien para decidir si la pieza estaba bien o mal."),

    ("avg_detection_ratio","Agujeros que sí se ven", "%",
     "En promedio, qué porcentaje de los agujeros esperados pudo ver\n"
     "la cámara. Si está muy por debajo de 100 %, revisar foco o luz."),

    ("align_fail_count",   "Veces que costó ubicar la chapa", "",
     "Cuántas veces el sistema tuvo dificultad para encontrar la chapa\n"
     "y tuvo que usar un método alternativo. Si se repite, avisar a mantenimiento."),

    ("last_position_diff", "Movimiento de la chapa", "px",
     "Cuánto se movió la chapa entre una foto y la siguiente.\n"
     "Sirve para ver qué tan rápido avanza la lámina."),
]

# Métricas que se grafican en la pestaña Historial
# (columna_db, etiqueta, color, y_label)
_CHART_DEF = [
    ("ok_pct",       "Piezas buenas (%)",       _OK,     "%"),
    ("insp_per_min", "Piezas por minuto",       _ACCENT, "piezas/min"),
    ("nok_streak",   "Fallas seguidas",         _NOK,    "cantidad"),
    ("camera_fps",   "Velocidad de la cámara",  _WARN,   "img/seg"),
    ("avg_detection_ratio", "Agujeros que sí se ven (%)", "#a78bfa", "%"),
    ("low_quality_pct", "Imágenes borrosas (%)", "#f97316", "%"),
]

_TIME_RANGES = [
    ("1 h",  1),
    ("4 h",  4),
    ("8 h",  8),
    ("24 h", 24),
    ("48 h", 48),
    ("7 d", 168),
    ("30 d", 720),
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
        self.setMinimumHeight(118)
        self.setStyleSheet(
            f"QWidget {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {_SURFACE_2}, stop:1 {_PANEL});"
            f"border-radius:14px;border:1px solid {_BORDER}; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(5)

        top_line = QFrame()
        top_line.setFixedHeight(4)
        top_line.setStyleSheet(
            f"background:{_HEADER_LINE};border:none;border-radius:2px;"
        )
        lay.addWidget(top_line)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"font-size:9px;color:{_MUTED};letter-spacing:1.4px;"
            "font-weight:700;background:transparent;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._val = QLabel("—")
        self._val.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setStyleSheet(f"color:{_MUTED};background:transparent;")

        lay.addWidget(lbl)
        lay.addWidget(self._val)
        if unit:
            u = QLabel(unit)
            u.setStyleSheet(
                f"font-size:9px;color:{_MUTED};background:transparent;"
            )
            u.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(u)

    def update(self, text: str, color: str = _TEXT) -> None:
        self._val.setText(text)
        self._val.setStyleSheet(
            f"color:{color};background:transparent;"
            "font-size:22px;font-weight:800;"
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
        scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:{_DARK}; }}"
            f"QScrollBar:vertical {{ background:{_DARK}; width:10px; margin:0; }}"
            f"QScrollBar::handle:vertical {{ background:{_BORDER}; border-radius:5px; min-height:30px; }}"
        )

        content = QWidget()
        content.setStyleSheet(f"background:{_DARK};")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(18)

        for sid in self._system.scanner_ids():
            num = sid.split("_")[-1]
            try:
                from src.utils.model_names import to_display
                model_txt = to_display(self._system.io.scanner_config(sid).get("model", ""))
            except Exception:
                model_txt = self._system.io.scanner_config(sid).get("model", "")
            grp = QGroupBox(f"SCANNER {num}   ·   {model_txt or '—'}")
            grp.setStyleSheet(self._grp_style())
            grid = QGridLayout(grp)
            grid.setContentsMargins(14, 22, 14, 14)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)

            cards: dict[str, _MetricCard] = {}
            for i, (mid, label, unit, tip) in enumerate(_METRICS_DEF):
                card = _MetricCard(label, unit, tip)
                card.setMinimumWidth(150)
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
            f"QGroupBox {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {_SURFACE}, stop:1 {_PANEL});border:1px solid {_BORDER};"
            f"border-radius:18px;margin-top:14px;padding-top:14px;"
            f"font-size:14px;font-weight:800;color:{_ACCENT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:18px;padding:0 10px;"
            f"background:{_DARK};border-radius:8px; }}"
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
        self._hours   = 168
        self._scanner = system.scanner_ids()[0] if system.scanner_ids() else ""
        self._range_btns: list[QPushButton] = []
        self._axes:   list = []
        self._canvas  = None
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        top_box = QFrame()
        top_box.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {_SURFACE}, stop:1 {_PANEL});"
            f"border:1px solid {_BORDER};border-radius:16px;"
        )
        top = QHBoxLayout(top_box)
        top.setContentsMargins(16, 14, 16, 14)
        top.setSpacing(10)

        sc_lbl = QLabel("Scanner:")
        sc_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;font-weight:700;")
        top.addWidget(sc_lbl)

        self._sc_combo = QComboBox()
        self._sc_combo.addItems(self._system.scanner_ids())
        self._sc_combo.setStyleSheet(
            f"background:{_CHIP};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:8px;padding:6px 10px;font-size:12px;font-weight:700;"
        )
        self._sc_combo.currentTextChanged.connect(self._on_scanner_changed)
        top.addWidget(self._sc_combo)

        top.addSpacing(16)
        rng_lbl = QLabel("Rango:")
        rng_lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;font-weight:700;")
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
        ref_btn.setFixedHeight(36)
        ref_btn.setStyleSheet(
            f"background:{_ACCENT};color:#03121f;font-weight:800;"
            "border-radius:10px;padding:0 18px;border:none;font-size:12px;"
        )
        ref_btn.clicked.connect(self._update_charts)
        top.addWidget(ref_btn)
        root.addWidget(top_box)

        self._hist_status = QLabel("")
        self._hist_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hist_status.setStyleSheet(
            f"background:{_SURFACE};color:{_MUTED};border:1px solid {_BORDER};"
            "border-radius:12px;padding:8px 14px;font-size:11px;font-weight:700;"
        )
        root.addWidget(self._hist_status)

        chart_box = QFrame()
        chart_box.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {_SURFACE}, stop:1 {_PANEL});"
            f"border:1px solid {_BORDER};border-radius:18px;"
        )
        chart_lay = QVBoxLayout(chart_box)
        chart_lay.setContentsMargins(14, 14, 14, 14)

        # ── área de gráficos ──────────────────────────────────────
        if _MPL:
            self._fig = Figure(figsize=(12, 6), facecolor=_SURFACE)
            self._axes = []
            n_plots = len(_CHART_DEF)
            for i in range(n_plots):
                ax = self._fig.add_subplot(3, 2, i + 1)
                self._axes.append(ax)
            self._fig.tight_layout(pad=2.5)

            self._canvas = FigureCanvas(self._fig)
            self._canvas.setStyleSheet(f"background:{_SURFACE};")
            chart_lay.addWidget(self._canvas, stretch=1)
            root.addWidget(chart_box, stretch=1)
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
            chart_lay.addWidget(msg, stretch=1)
            root.addWidget(chart_box, stretch=1)

    def _range_style(self, active: bool) -> str:
        bg = _ACCENT if active else _PANEL
        fg = "#000000" if active else _TEXT
        return (
            f"background:{bg};color:{fg};border:1px solid {_BORDER};"
            "border-radius:9px;font-size:11px;padding:4px 12px;"
            "font-weight:800;"
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
        using_fallback = False
        latest_ts = self._system.metrics.latest_timestamp(self._scanner)

        if rows:
            status_text = (
                f"{self._scanner.upper()} · Ultimas {self._hours} h · "
                f"{len(rows)} muestras"
            )
        elif latest_ts is not None:
            rows = self._system.metrics.query_recent(self._scanner, limit=240)
            using_fallback = bool(rows)
            last_dt = datetime.fromtimestamp(latest_ts)
            status_text = (
                f"{self._scanner.upper()} · Sin datos en {self._hours} h · "
                f"mostrando ultimos registros hasta {last_dt:%d/%m/%Y %H:%M}"
            )
        else:
            status_text = (
                f"{self._scanner.upper()} · Todavia no hay historial guardado"
            )

        self._hist_status.setText(status_text)

        for ax, (col, lbl, color, ylabel) in zip(self._axes, _CHART_DEF):
            ax.cla()
            ax.set_facecolor(_SURFACE_2)
            ax.set_title(lbl, fontsize=10, pad=8, color=_TEXT, fontweight="bold")
            ax.set_ylabel(ylabel, fontsize=8, color=_MUTED)
            ax.tick_params(colors=_MUTED, labelsize=7)
            for spine in ax.spines.values():
                spine.set_color(_BORDER)
            ax.grid(True, color=_BORDER, linewidth=0.5, linestyle="--", alpha=0.55)

            if not rows:
                ax.text(
                    0.5,
                    0.5,
                    "Sin datos historicos\ndisponibles",
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                    color=_MUTED,
                    fontsize=9,
                )
                continue

            xs = [datetime.fromtimestamp(r["ts"]) for r in rows]
            ys = [r.get(col) or 0.0 for r in rows]

            ax.plot(xs, ys, color=color, linewidth=2.0,
                    marker="o", markersize=3.2, zorder=3)
            ax.fill_between(xs, ys, alpha=0.14, color=color)

            time_format = "%d/%m %H:%M" if using_fallback else "%H:%M"
            ax.xaxis.set_major_formatter(mdates.DateFormatter(time_format))
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
                background:{_PANEL};border:1px solid {_BORDER};border-radius:14px;
            }}
            QTabBar::tab {{
                background:{_SURFACE};color:{_MUTED};
                padding:10px 24px;font-size:12px;border-radius:10px;margin-right:6px;
                border:1px solid {_BORDER};font-weight:700;
            }}
            QTabBar::tab:selected {{
                background:{_ACCENT};color:#03121f;font-weight:800;
                border:1px solid {_ACCENT};
            }}
        """)

        self._rt_tab   = _RealTimeTab(self._system)
        self._hist_tab = _HistoricalTab(self._system)

        self._tabs.addTab(self._rt_tab,   "Tiempo Real")
        self._tabs.addTab(self._hist_tab, "Historial")

        root.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(98)
        header.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #05101f, stop:0.35 #0c2340, stop:0.7 #0f3b5f, stop:1 #05101f);"
            "border-radius:16px; border:1px solid #164e63; }"
        )
        outer = QHBoxLayout(header)
        outer.setContentsMargins(22, 0, 22, 0)
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
            "color:#f1f5f9;font-size:28px;font-weight:800;"
            "letter-spacing:5px;background:transparent;"
        )
        s = QLabel("PANEL DE METRICAS  ·  MONITOREO Y DIAGNOSTICO")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet(
            "color:#cbd5e1;font-size:10px;font-weight:700;letter-spacing:2px;"
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
