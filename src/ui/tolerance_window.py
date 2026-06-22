"""
Ventana de ajuste de tolerancias de inspección por scanner.

Expone únicamente los parámetros seguros para el operario:
  - Cuántos agujeros faltantes para marcar NOK
  - Cuántos frames consecutivos antes de parada automática
  - Tolerancia de posición en píxeles
  - Umbral de inclinación de la chapa
  - Frames NOK consecutivos antes de FAULT
  - Segundos de evidencia a grabar

Los parámetros técnicos (geometría del patrón, umbrales de detección,
alineación, etc.) solo se modifican desde el modo servicio.
"""

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.controller.system import InspectionSystem
from src.utils.config import load_tolerances, save_scanner_overrides
from src.utils.model_names import to_display

from src.utils.paths import app_root
_ROOT = app_root()

# ------------------------------------------------------------------
# Paleta (coherente con metrics_window.py / service.py)
# ------------------------------------------------------------------
_DARK   = "#0f172a"
_PANEL  = "#1e293b"
_CARD   = "#243044"
_BORDER = "#334155"
_TEXT   = "#f1f5f9"
_MUTED  = "#94a3b8"
_ACCENT = "#38bdf8"
_OK     = "#4ade80"
_WARN   = "#fbbf24"
_NOK    = "#f87171"

# ------------------------------------------------------------------
# Definición de parámetros expuestos al operario
# ------------------------------------------------------------------
# Solo incluir params cuyo rango restringido NO puede romper la detección.
_PARAMS: list[dict[str, Any]] = [
    dict(
        key="frame_missing_nok_threshold",
        label="Agujeros faltantes para NOK",
        desc=(
            "Cuántos agujeros deben faltar en un frame para clasificarlo como NOK. "
            "Más bajo = más estricto."
        ),
        unit="agujeros",
        vtype="int",
        vmin=1, vmax=60, vstep=1,
    ),
    dict(
        key="machine_stop_missing_frames",
        label="Frames persistentes para parada",
        desc=(
            "Cuántos frames seguidos con el MISMO agujero faltante antes de detener "
            "la línea. Mínimo 2 (nunca para por un solo frame)."
        ),
        unit="frames",
        vtype="int",
        vmin=2, vmax=20, vstep=1,
    ),
    dict(
        key="consecutive_nok_frames",
        label="Frames NOK para FAULT",
        desc=(
            "Cuántos frames NOK seguidos antes de disparar FAULT y detener la línea. "
            "Valores grandes (p.ej. 9999) deshabilitan el FAULT automático."
        ),
        unit="frames",
        vtype="int",
        vmin=2, vmax=9999, vstep=1,
    ),
    dict(
        key="pattern_align_severe_abs_max_px",
        label="Sensibilidad ante chapa torcida",
        desc=(
            "Si la chapa se ve muy desviada o torcida en una sola foto, la máquina "
            "se detiene de inmediato, sin esperar varias fotos seguidas. "
            "Número MÁS BAJO = se detiene más fácil (más sensible). "
            "Número MÁS ALTO = solo se detiene ante desvíos más grandes."
        ),
        unit="px",
        vtype="float",
        vmin=5.0, vmax=60.0, vstep=1.0, decimals=1,
    ),
]


# ------------------------------------------------------------------
# Panel de un scanner
# ------------------------------------------------------------------

class _ScannerTolerancePanel(QWidget):
    def __init__(
        self,
        scanner_id: str,
        system: InspectionSystem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = scanner_id
        self._system = system
        self._spinboxes: dict[str, QSpinBox | QDoubleSpinBox] = {}
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------

    def _model(self) -> str:
        return self._system.io.scanner_config(self._id).get("model", "")

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_PANEL};")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # ── Título: scanner + tipo de placa ──────────────────────────
        _num = self._id.split("_")[-1]
        self._model_lbl = QLabel(f"Scanner {_num}  ·  —")
        self._model_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._model_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:14px;font-weight:700;"
            f"letter-spacing:1px;background:transparent;"
        )
        root.addWidget(self._model_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_BORDER};")
        root.addWidget(sep)

        # ── Parámetros ────────────────────────────────────────────────
        for p in _PARAMS:
            root.addWidget(self._param_card(p))

        root.addStretch()

        # ── Botón guardar ─────────────────────────────────────────────
        self._save_btn = QPushButton(f"Guardar {self._id.replace('_',' ').upper()}")
        self._save_btn.setMinimumHeight(44)
        self._save_btn.setStyleSheet(
            f"background:{_ACCENT};color:#0f172a;font-weight:700;"
            "border-radius:8px;font-size:13px;border:none;"
        )
        self._save_btn.clicked.connect(self._on_save)
        root.addWidget(self._save_btn)

    def _param_card(self, p: dict) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_CARD};border-radius:8px;"
            f"border:1px solid {_BORDER};}}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        # Fila superior: label + spinbox + unidad
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        lbl = QLabel(p["label"])
        lbl.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:600;"
            "background:transparent;border:none;"
        )
        top_row.addWidget(lbl, stretch=1)

        if p["vtype"] == "int":
            sb: QSpinBox | QDoubleSpinBox = QSpinBox()
            sb.setRange(p["vmin"], p["vmax"])
            sb.setSingleStep(p["vstep"])
        else:
            sb = QDoubleSpinBox()
            sb.setRange(p["vmin"], p["vmax"])
            sb.setSingleStep(p["vstep"])
            sb.setDecimals(p.get("decimals", 1))

        sb.setFixedWidth(110)
        sb.setFixedHeight(36)
        sb.setStyleSheet(
            f"QSpinBox, QDoubleSpinBox {{"
            f"  background:#0f172a; color:{_TEXT};"
            f"  border:1px solid {_BORDER}; border-radius:4px;"
            f"  font-size:14px; font-weight:700; padding:2px 4px;"
            f"}}"
            f"QSpinBox::up-button, QDoubleSpinBox::up-button {{"
            f"  width:22px; border-left:1px solid {_BORDER};"
            f"  background:#243044; border-top-right-radius:4px;"
            f"}}"
            f"QSpinBox::down-button, QDoubleSpinBox::down-button {{"
            f"  width:22px; border-left:1px solid {_BORDER};"
            f"  background:#243044; border-bottom-right-radius:4px;"
            f"}}"
            f"QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            f"QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{"
            f"  background:#38bdf8;"
            f"}}"
            f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{"
            f"  width:8px; height:8px;"
            f"}}"
        )
        top_row.addWidget(sb)

        unit_lbl = QLabel(p["unit"])
        unit_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;background:transparent;border:none;"
        )
        unit_lbl.setFixedWidth(44)
        top_row.addWidget(unit_lbl)

        lay.addLayout(top_row)

        # Descripción
        desc = QLabel(p["desc"])
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color:{_MUTED};font-size:10px;background:transparent;border:none;"
        )
        lay.addWidget(desc)

        self._spinboxes[p["key"]] = sb
        return card

    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        model = self._model()
        _num = self._id.split("_")[-1]
        display = to_display(model) if model else "—"
        self._model_lbl.setText(f"Scanner {_num}  ·  {display}")
        if not model:
            return
        tols = load_tolerances(model, scanner_id=self._id)
        for p in _PARAMS:
            sb = self._spinboxes[p["key"]]
            raw = tols.get(p["key"])
            if raw is None:
                continue
            if isinstance(sb, QDoubleSpinBox):
                sb.setValue(float(raw))
            else:
                sb.setValue(int(raw))

    def _on_save(self) -> None:
        model = self._model()
        if not model:
            QMessageBox.warning(self, "Sin modelo", f"No hay modelo configurado para {self._id}.")
            return

        updates: dict[str, Any] = {}
        for p in _PARAMS:
            sb = self._spinboxes[p["key"]]
            updates[p["key"]] = sb.value()

        # Confirmación si consecutive_nok_frames está en valor de calibración
        nok_frames = updates.get("consecutive_nok_frames", 0)
        if isinstance(nok_frames, (int, float)) and int(nok_frames) >= 500:
            resp = QMessageBox.question(
                self,
                "FAULT desactivado",
                f"consecutive_nok_frames = {int(nok_frames)}\n\n"
                "Con este valor el FAULT automático está prácticamente desactivado.\n"
                "¿Guardar de todas formas?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        try:
            save_scanner_overrides(self._id, updates)
            # Fuerza recarga de tolerancias en el scanner activo
            scanner = self._system.scanner(self._id)
            scanner.set_model(model)
            QMessageBox.information(
                self,
                "Guardado",
                f"Tolerancias de {to_display(model)} guardadas correctamente.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"No se pudo guardar:\n{exc}")


# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------

class ToleranceWindow(QMainWindow):
    def __init__(
        self, system: InspectionSystem, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._system = system
        self.setWindowTitle("Tolerancias — DEFYVISION")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.setStyleSheet(f"background:{_DARK};")
        self.resize(900, 780)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background:{_DARK};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Título ───────────────────────────────────────────────────
        title = QLabel("TOLERANCIAS")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color:{_TEXT};font-size:20px;font-weight:700;"
            f"letter-spacing:4px;background:{_PANEL};"
            f"border-radius:8px;padding:10px;"
        )
        root.addWidget(title)

        # ── Aviso ─────────────────────────────────────────────────────
        warn = QLabel(
            "⚠   Solo modificar si el análisis tiene demasiados falsos errores "
            "o no detecta eficientemente defectos reales."
        )
        warn.setWordWrap(True)
        warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warn.setStyleSheet(
            f"color:#78350f;font-size:11px;font-weight:600;"
            f"background:#fef3c7;border:1px solid #fbbf24;"
            "border-radius:6px;padding:8px 14px;"
        )
        root.addWidget(warn)

        # ── Paneles de scanners ────────────────────────────────────────
        panels_row = QHBoxLayout()
        panels_row.setSpacing(12)

        for sid in self._system.scanner_ids():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet(
                f"QScrollArea{{border:none;background:{_PANEL};}}"
                "QScrollBar:vertical{width:6px;background:#1e293b;}"
                "QScrollBar::handle:vertical{background:#334155;border-radius:3px;}"
            )
            panel = _ScannerTolerancePanel(sid, self._system)
            scroll.setWidget(panel)
            panels_row.addWidget(scroll)

        root.addLayout(panels_row, stretch=1)

        # ── Botón recargar ────────────────────────────────────────────
        reload_btn = QPushButton("Recargar valores actuales")
        reload_btn.setFixedHeight(32)
        reload_btn.setStyleSheet(
            f"background:transparent;color:{_MUTED};"
            f"border:1px solid {_BORDER};border-radius:6px;font-size:11px;"
        )
        reload_btn.clicked.connect(self._reload_all)
        root.addWidget(reload_btn)

    def _reload_all(self) -> None:
        central = self.centralWidget()
        for scroll in central.findChildren(QScrollArea):
            panel = scroll.widget()
            if isinstance(panel, _ScannerTolerancePanel):
                panel._load_values()
