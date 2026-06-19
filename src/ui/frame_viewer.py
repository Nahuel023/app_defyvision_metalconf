"""
Visor de frames para el operario.

Permite navegar dos tipos de evidencia sin acceso a modo servicio:
  • Paradas: carpetas DD-MM-YYYY_STOP_N con pre/post frames y manifest.json
  • OK recientes: buffer circular de las últimas inspecciones correctas

Diseño: industrial, oscuro, lectura fácil. Sin controles de desarrollo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import shutil
import yaml
import cv2
import numpy as np
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.utils.paths import app_root
_ROOT = app_root()

# ── Paleta ────────────────────────────────────────────────────────────────────
_BG      = "#0d1117"
_PANEL   = "#161b22"
_CARD    = "#21262d"
_BORDER  = "#30363d"
_TEXT    = "#f0f6fc"
_MUTED   = "#8b949e"
_ACCENT  = "#3b82f6"
_OK_CLR  = "#3fb950"
_NOK_CLR = "#f85149"
_WARN    = "#d29922"

_EVENTS_DIR     = _ROOT / "data" / "events"
_OK_BUF_BASE    = _ROOT / "data" / "output" / "ok_buffer"
_NOK_DIR        = _ROOT / "data" / "output" / "nok"
_TIMELINE_BASE  = _ROOT / "data" / "output" / "timeline"

# Colores por tag de timeline
_TL_COLORS = {
    "OK":   _OK_CLR,
    "NOK":  _NOK_CLR,
    "STOP": "#f97316",   # naranja — parada de máquina
    "LQ":   "#64748b",   # gris — baja calidad / borroso
}

_THUMB_W = 90
_THUMB_H = 60

# Tope de frames que el visor carga/miniaturiza de una sola vez. Carpetas como
# data/output/nok/ no tienen buffer circular y pueden acumular miles de
# imagenes si la maquina corre mucho tiempo sin limpieza manual; sin este tope
# el visor crea una miniatura por cada archivo y se cuelga.
_MAX_VIEWER_FRAMES = 300


# ── Helper ────────────────────────────────────────────────────────────────────

def _bgr_to_pixmap(img: np.ndarray, max_w: int, max_h: int) -> QPixmap:
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 0.999:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def _load_thumb(path: Path) -> QPixmap:
    img = cv2.imread(str(path))
    if img is None:
        return QPixmap()
    return _bgr_to_pixmap(img, _THUMB_W, _THUMB_H)


def _event_summary(event_dir: Path) -> dict:
    """Lee manifest.json y devuelve un dict con los campos clave."""
    manifest_path = event_dir / "manifest.json"
    m: dict = {}
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    pre  = sorted(event_dir.glob("frame_*.jpg"))
    post = sorted(f for f in event_dir.glob("post_*.jpg") if "_overlay" not in f.name)
    return {
        "dir": event_dir,
        "name": event_dir.name,
        "timestamp": m.get("timestamp", ""),
        "scanner_id": m.get("scanner_id", ""),
        "event_type": m.get("event_type", ""),
        "reason": m.get("reason", ""),
        "pre_frames": pre,
        "post_frames": post,
        "all_frames": pre + post,
        "pre_count": len(pre),
        "post_count": len(post),
        "total_bytes": m.get("total_bytes", 0),
    }


def _format_ts(ts_iso: str) -> str:
    if not ts_iso:
        return "—"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts_iso)
        return dt.strftime("%d/%m/%Y  %H:%M:%S")
    except Exception:
        return ts_iso[:16]


def _scanner_display(scanner_id: str) -> str:
    try:
        from src.utils.model_names import to_display
        # scanner_id is like 'scanner_1'; we need the model
        # We can't always get the model here without the system, so just show the ID nicely
    except Exception:
        pass
    num = scanner_id.split("_")[-1] if "_" in scanner_id else scanner_id
    return f"Scanner {num}"


# ── Worker de carga de miniaturas ─────────────────────────────────────────────

class _ThumbLoader(QThread):
    """Carga miniaturas en segundo plano para no congelar la UI."""
    thumb_ready = pyqtSignal(int, QPixmap)  # (index, pixmap)

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self):
        for i, p in enumerate(self._paths):
            pix = _load_thumb(p)
            self.thumb_ready.emit(i, pix)


# ── Widget: visor de un único frame con zoom y pan ───────────────────────────

class ZoomableFrameView(QWidget):
    """Visor de frame con zoom (rueda) y pan (arrastrar). API: set_image / clear_image / fit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._scale: float = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._drag_start: Optional[QPointF] = None
        self._drag_offset: Optional[QPointF] = None
        self.setMinimumSize(400, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background:#000000;border-radius:6px;border:2px solid {_BORDER};")
        self.setMouseTracking(True)

    def set_image(self, img: np.ndarray) -> None:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)
        self.fit()

    def clear_image(self) -> None:
        self._pixmap = None
        self.update()

    def fit(self) -> None:
        if self._pixmap is None:
            return
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw == 0 or ph == 0 or w == 0 or h == 0:
            return
        self._scale = min(w / pw, h / ph)
        self._offset = QPointF(
            (w - pw * self._scale) / 2.0,
            (h - ph * self._scale) / 2.0,
        )
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap is not None:
            self.fit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._pixmap is not None:
            pw = self._pixmap.width() * self._scale
            ph = self._pixmap.height() * self._scale
            target = QRectF(self._offset.x(), self._offset.y(), pw, ph)
            painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
            painter.setPen(QColor(_MUTED))
            painter.setFont(QFont("Segoe UI", 9))
            badge = f"{int(round(self._scale * 100))}%"
            painter.drawText(self.rect().adjusted(0, 4, -6, 0),
                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, badge)

    def wheelEvent(self, event) -> None:
        if self._pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        new_scale = max(0.05, min(self._scale * factor, 30.0))
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


def _get_model_for_scanner(scanner_id: str) -> str:
    """Lee io_map.yaml y devuelve el modelo activo para el scanner dado."""
    try:
        io_map_path = _ROOT / "config" / "io_map.yaml"
        cfg = yaml.safe_load(io_map_path.read_text(encoding="utf-8"))
        return cfg.get(scanner_id, {}).get("model", "modelo_A")
    except Exception:
        return "modelo_A"


class _InspectWorker(QThread):
    """Corre inspect_image en background y emite el overlay resultante."""
    done = pyqtSignal(np.ndarray)  # overlay BGR

    def __init__(self, frame_path: Path, model: str, scanner_id: str, parent=None):
        super().__init__(parent)
        self._path = frame_path
        self._model = model
        self._scanner_id = scanner_id

    def run(self):
        try:
            from src.inspection import inspect_image
            result = inspect_image(self._model, str(self._path),
                                   scanner_id=self._scanner_id)
            if result.overlay is not None:
                self.done.emit(result.overlay)
        except Exception:
            pass


# ── Panel de navegación de un evento ─────────────────────────────────────────

class _EventNavPanel(QWidget):
    """Visor de frames de un evento: imagen grande + tira de miniaturas + navegación."""

    folder_deleted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: list[Path] = []
        self._current: int = 0
        self._loader: Optional[_ThumbLoader] = None
        self._thumb_widgets: list[QLabel] = []
        self._show_overlay: bool = True   # toggle overlay/raw
        self._scanner_id: str = ""        # para análisis on-demand
        self._model: str = ""
        self._is_event_mode: bool = False  # True = frames de evento (análisis on-demand)
        self._inspect_worker: Optional[_InspectWorker] = None
        self._current_dir: Optional[Path] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Info del evento ───────────────────────────────────────────
        self._info_lbl = QLabel("Selecciona un evento de la lista")
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:11px;background:{_CARD};"
            f"border-radius:5px;padding:5px;border:1px solid {_BORDER};"
        )
        root.addWidget(self._info_lbl)

        # ── Frame grande ──────────────────────────────────────────────
        self._view = ZoomableFrameView()
        root.addWidget(self._view, stretch=1)

        # ── Controles de navegación ───────────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(10)

        self._prev_btn = QPushButton("◀  Anterior")
        self._prev_btn.setMinimumHeight(36)
        self._prev_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};"
            f"border-radius:6px; font-size:12px; font-weight:600; padding:0 16px; }}"
            f"QPushButton:hover {{ background:{_ACCENT}33; border-color:{_ACCENT}; }}"
            f"QPushButton:disabled {{ color:#374151; border-color:#1f2937; }}"
        )
        self._prev_btn.clicked.connect(self._go_prev)
        nav.addWidget(self._prev_btn)

        self._pos_lbl = QLabel("—")
        self._pos_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pos_lbl.setMinimumWidth(140)
        self._pos_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:13px;font-weight:700;background:transparent;"
        )
        nav.addWidget(self._pos_lbl, stretch=1)

        self._next_btn = QPushButton("Siguiente  ▶")
        self._next_btn.setMinimumHeight(36)
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};"
            f"border-radius:6px; font-size:12px; font-weight:600; padding:0 16px; }}"
            f"QPushButton:hover {{ background:{_ACCENT}33; border-color:{_ACCENT}; }}"
            f"QPushButton:disabled {{ color:#374151; border-color:#1f2937; }}"
        )
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)

        self._overlay_btn = QPushButton("👁  Con overlay")
        self._overlay_btn.setMinimumHeight(36)
        self._overlay_btn.setCheckable(True)
        self._overlay_btn.setChecked(True)
        self._overlay_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:{_MUTED}; border:1px solid {_BORDER};"
            f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
            f"QPushButton:checked {{ background:{_ACCENT}; color:#ffffff; border-color:{_ACCENT}; }}"
            f"QPushButton:hover:!checked {{ border-color:{_ACCENT}; color:{_TEXT}; }}"
        )
        self._overlay_btn.clicked.connect(self._toggle_overlay)
        nav.addWidget(self._overlay_btn)

        self._fit_btn = QPushButton("⊡  Ajustar")
        self._fit_btn.setMinimumHeight(36)
        self._fit_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:{_MUTED}; border:1px solid {_BORDER};"
            f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
            f"QPushButton:hover {{ border-color:#0f766e; color:{_TEXT}; }}"
        )
        self._fit_btn.clicked.connect(lambda: self._view.fit())
        nav.addWidget(self._fit_btn)

        self._del_frame_btn = QPushButton("✕  Borrar frame")
        self._del_frame_btn.setMinimumHeight(36)
        self._del_frame_btn.setEnabled(False)
        self._del_frame_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:#f87171; border:1px solid #7f1d1d;"
            f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
            f"QPushButton:hover {{ background:#7f1d1d; color:#ffffff; border-color:#dc2626; }}"
            f"QPushButton:disabled {{ color:#374151; border-color:#1f2937; }}"
        )
        self._del_frame_btn.clicked.connect(self._delete_current_frame)
        nav.addWidget(self._del_frame_btn)

        self._del_folder_btn = QPushButton("✕✕  Borrar carpeta")
        self._del_folder_btn.setMinimumHeight(36)
        self._del_folder_btn.setEnabled(False)
        self._del_folder_btn.setStyleSheet(
            f"QPushButton {{ background:{_CARD}; color:#fca5a5; border:1px solid #991b1b;"
            f"border-radius:6px; font-size:11px; font-weight:600; padding:0 12px; }}"
            f"QPushButton:hover {{ background:#991b1b; color:#ffffff; border-color:#ef4444; }}"
            f"QPushButton:disabled {{ color:#374151; border-color:#1f2937; }}"
        )
        self._del_folder_btn.clicked.connect(self._delete_current_folder)
        nav.addWidget(self._del_folder_btn)

        root.addLayout(nav)

        # ── Tira de miniaturas ────────────────────────────────────────
        thumb_scroll = QScrollArea()
        thumb_scroll.setFixedHeight(_THUMB_H + 20)
        thumb_scroll.setWidgetResizable(True)
        thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        thumb_scroll.setStyleSheet(
            f"QScrollArea {{ background:{_PANEL}; border:1px solid {_BORDER}; border-radius:5px; }}"
            "QScrollBar:horizontal { height:8px; background:#0d1117; }"
            "QScrollBar::handle:horizontal { background:#30363d; border-radius:4px; }"
        )
        self._thumb_container = QWidget()
        self._thumb_container.setStyleSheet(f"background:{_PANEL};")
        self._thumb_lay = QHBoxLayout(self._thumb_container)
        self._thumb_lay.setContentsMargins(4, 4, 4, 4)
        self._thumb_lay.setSpacing(4)
        self._thumb_lay.addStretch()
        thumb_scroll.setWidget(self._thumb_container)
        root.addWidget(thumb_scroll)

    # ── API pública ───────────────────────────────────────────────────

    def load_event(self, summary: dict) -> None:
        self._frames = summary["all_frames"]
        self._current = 0
        self._scanner_id = summary.get("scanner_id", "")
        self._model = _get_model_for_scanner(self._scanner_id)
        self._is_event_mode = True
        self._current_dir = summary.get("dir")

        # Separar pre/post para colorear la tira
        n_pre = summary["pre_count"]

        # Info del evento
        ts = _format_ts(summary["timestamp"])
        scanner = _scanner_display(summary["scanner_id"])
        reason  = summary["reason"] or summary["event_type"]
        total   = len(self._frames)
        pre_c   = summary["pre_count"]
        post_c  = summary["post_count"]
        self._info_lbl.setText(
            f"{ts}   ·   {scanner}   ·   {reason}   ·   "
            f"{pre_c} frames previos  +  {post_c} frames posteriores  =  {total} total"
        )

        # Limpiar miniaturas anteriores
        self._clear_thumbs()
        for i in range(total):
            lbl = QLabel()
            lbl.setFixedSize(_THUMB_W, _THUMB_H)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Colorear borde: azul = pre-evento, naranja = post-evento
            border_color = _ACCENT if i < n_pre else _WARN
            lbl.setStyleSheet(
                f"background:#000;border:2px solid {border_color};border-radius:3px;"
            )
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda e, idx=i: self._go_to(idx)
            self._thumb_lay.insertWidget(self._thumb_lay.count() - 1, lbl)
            self._thumb_widgets.append(lbl)

        # Cargar miniaturas en segundo plano
        if self._loader is not None:
            self._loader.quit()
            self._loader.wait(200)
        self._loader = _ThumbLoader(self._frames)
        self._loader.thumb_ready.connect(self._on_thumb_ready)
        self._loader.start()

        self._show_frame(0)

    def load_ok_buffer(self, frames: list[Path], scanner_label: str,
                       folder_dir: Optional[Path] = None) -> None:
        # Orden cronologico ascendente (el llamador puede pasar cualquier orden)
        # y tope de cantidad: carpetas sin buffer circular (ej. nok/) pueden
        # acumular miles de archivos y colgar la UI si se cargan todos.
        frames = sorted(frames, key=lambda f: f.stat().st_mtime)
        total = len(frames)
        if total > _MAX_VIEWER_FRAMES:
            frames = frames[-_MAX_VIEWER_FRAMES:]
        self._frames = frames
        self._current = 0
        self._is_event_mode = False
        self._current_dir = folder_dir
        newest_ts = ""
        if frames:
            try:
                newest_ts = "  ·  último: " + datetime.fromtimestamp(
                    frames[-1].stat().st_mtime).strftime("%H:%M:%S")
            except Exception:
                pass
        count_txt = f"{len(frames)} frames OK" if total == len(frames) \
            else f"{len(frames)} de {total} frames OK (mostrando los más recientes)"
        self._info_lbl.setText(
            f"{scanner_label}   ·   {count_txt}"
            f"   ◀ antiguo — reciente ▶{newest_ts}"
        )
        self._clear_thumbs()
        for i in range(len(frames)):
            lbl = QLabel()
            lbl.setFixedSize(_THUMB_W, _THUMB_H)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"background:#000;border:2px solid {_OK_CLR};border-radius:3px;"
            )
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda e, idx=i: self._go_to(idx)
            self._thumb_lay.insertWidget(self._thumb_lay.count() - 1, lbl)
            self._thumb_widgets.append(lbl)
        if self._loader is not None:
            self._loader.quit()
            self._loader.wait(200)
        self._loader = _ThumbLoader(frames)
        self._loader.thumb_ready.connect(self._on_thumb_ready)
        self._loader.start()
        self._show_frame(len(frames) - 1)   # ir al más reciente (derecha)

    def load_timeline(self, frames: list[Path], scanner_label: str) -> None:
        """Carga el buffer cronológico completo con colores por status."""
        frames = sorted(frames, key=lambda f: f.stat().st_mtime)
        total = len(frames)
        if total > _MAX_VIEWER_FRAMES:
            frames = frames[-_MAX_VIEWER_FRAMES:]
        self._frames = frames
        self._current = 0
        self._is_event_mode = False
        self._current_dir = frames[0].parent if frames else None

        counts = {}
        for f in frames:
            tag = f.stem.split("_", 1)[1] if "_" in f.stem else "OK"
            counts[tag] = counts.get(tag, 0) + 1
        summary_parts = [f"{c} {t}" for t, c in sorted(counts.items())]
        newest_ts = ""
        if frames:
            try:
                newest_ts = "  ·  último: " + datetime.fromtimestamp(
                    frames[-1].stat().st_mtime).strftime("%H:%M:%S")
            except Exception:
                pass
        count_txt = f"{len(frames)} frames" if total == len(frames) \
            else f"{len(frames)} de {total} frames (mostrando los más recientes)"
        self._info_lbl.setText(
            f"{scanner_label}   ·   {count_txt}   ({', '.join(summary_parts)})"
            f"   ◀ antiguo — reciente ▶{newest_ts}"
        )

        self._clear_thumbs()
        for i, path in enumerate(frames):
            tag = path.stem.split("_", 1)[1] if "_" in path.stem else "OK"
            color = _TL_COLORS.get(tag, _MUTED)
            lbl = QLabel()
            lbl.setFixedSize(_THUMB_W, _THUMB_H)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"background:#000;border:2px solid {color};border-radius:3px;"
            )
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda e, idx=i: self._go_to(idx)
            self._thumb_lay.insertWidget(self._thumb_lay.count() - 1, lbl)
            self._thumb_widgets.append(lbl)

        if self._loader is not None:
            self._loader.quit()
            self._loader.wait(200)
        self._loader = _ThumbLoader(frames)
        self._loader.thumb_ready.connect(self._on_thumb_ready)
        self._loader.start()
        self._show_frame(len(frames) - 1)   # ir al más reciente (derecha)

    # ── Internos ──────────────────────────────────────────────────────

    def _clear_thumbs(self) -> None:
        for w in self._thumb_widgets:
            self._thumb_lay.removeWidget(w)
            w.deleteLater()
        self._thumb_widgets.clear()

    def _on_thumb_ready(self, idx: int, pix: QPixmap) -> None:
        if idx < len(self._thumb_widgets) and not pix.isNull():
            self._thumb_widgets[idx].setPixmap(
                pix.scaled(_THUMB_W - 4, _THUMB_H - 4,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            )

    def _show_frame(self, idx: int) -> None:
        if not self._frames:
            self._view.clear_image()
            self._pos_lbl.setText("Sin frames")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            self._del_frame_btn.setEnabled(False)
            self._del_folder_btn.setEnabled(False)
            return
        self._del_frame_btn.setEnabled(True)
        self._del_folder_btn.setEnabled(self._current_dir is not None)
        idx = max(0, min(idx, len(self._frames) - 1))
        self._current = idx
        path = self._frames[idx]

        if self._is_event_mode and self._show_overlay:
            # Análisis on-demand: mostrar frame crudo inmediatamente y lanzar worker
            img = cv2.imread(str(path))
            if img is not None:
                self._view.set_image(img)
            self._pos_lbl.setText(f"Frame  {idx + 1}  /  {len(self._frames)}  ·  Analizando…")
            self._launch_inspect(path)
        else:
            img = cv2.imread(str(self._resolve_frame_path(path)))
            if img is not None:
                self._view.set_image(img)

        n = len(self._frames)
        if not (self._is_event_mode and self._show_overlay):
            _tag_suffix = ""
            _stem = path.stem
            if "_" in _stem and not _stem.startswith("ok_"):
                _tag = _stem.split("_", 1)[1]
                if _tag in _TL_COLORS:
                    _tag_suffix = f"  ·  {_tag}"
            try:
                _ts = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
                _tag_suffix += f"  ·  {_ts}"
            except Exception:
                pass
            _newest = "  ★" if idx == n - 1 else ""
            self._pos_lbl.setText(f"Frame  {idx + 1}  /  {n}{_tag_suffix}{_newest}")
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < n - 1)

        # Resaltar miniatura activa
        for i, w in enumerate(self._thumb_widgets):
            ss = w.styleSheet()
            if i == idx:
                if f"border:3px solid #ffffff" not in ss:
                    w.setStyleSheet(ss.replace("border:2px", "border:3px") + "opacity:1.0;")
            else:
                if "border:3px" in ss:
                    w.setStyleSheet(ss.replace("border:3px", "border:2px"))

    def _launch_inspect(self, path: Path) -> None:
        """Lanza análisis on-demand en background para frames de evento."""
        if self._inspect_worker is not None and self._inspect_worker.isRunning():
            self._inspect_worker.done.disconnect()
            self._inspect_worker.quit()
        self._inspect_worker = _InspectWorker(path, self._model, self._scanner_id)
        self._inspect_worker.done.connect(self._on_inspect_done)
        self._inspect_worker.start()

    def _on_inspect_done(self, overlay: np.ndarray) -> None:
        """Recibe el overlay analizado y lo muestra si el frame no cambió."""
        self._view.set_image(overlay)
        n = len(self._frames)
        self._pos_lbl.setText(f"Frame  {self._current + 1}  /  {n}")

    def _toggle_overlay(self) -> None:
        self._show_overlay = self._overlay_btn.isChecked()
        self._show_frame(self._current)

    def _resolve_frame_path(self, path: Path) -> Path:
        """Devuelve la ruta del frame a mostrar según el toggle overlay/raw.

        Casos soportados:
          - ok_NNNN.jpg          ↔  ok_NNNN_raw.jpg
          - *_overlay.png/jpg    ↔  *_raw.jpg
          - post_NNNN.jpg        ↔  post_NNNN_overlay.jpg  (invertido: raw→overlay)
          - frame_NNNN.jpg       — pre-evento, no hay overlay disponible
        """
        stem = path.stem
        parent = path.parent

        if self._show_overlay:
            # Queremos overlay: si el archivo YA es overlay devolver tal cual.
            # Para frames crudos (post_NNNN, ok_NNNN, frame_NNNN) buscar versión overlay.
            if "_overlay" in stem or "_raw" in stem:
                return path  # ya es overlay o no aplica
            # post_NNNN → post_NNNN_overlay.jpg
            overlay = parent / (stem + "_overlay.jpg")
            if overlay.exists():
                return overlay
            # ok_NNNN → ok_NNNN.jpg ya es overlay (el overlay se guarda como ok_NNNN.jpg)
            return path
        else:
            # Queremos raw
            if stem.endswith("_overlay"):
                raw = parent / (stem[: -len("_overlay")] + "_raw.jpg")
                return raw if raw.exists() else path
            raw = parent / (stem + "_raw.jpg")
            return raw if raw.exists() else path

    def _delete_current_frame(self) -> None:
        if not self._frames or self._current >= len(self._frames):
            return
        path = self._frames[self._current]
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
            # También borrar archivos asociados (_raw, _overlay)
            for suffix in ("_raw.jpg", "_overlay.jpg", "_overlay.png"):
                assoc = path.parent / (path.stem + suffix)
                assoc.unlink(missing_ok=True)
            self._frames.pop(self._current)
            # Limpiar thumbnail
            if self._current < len(self._thumb_widgets):
                w = self._thumb_widgets.pop(self._current)
                self._thumb_lay.removeWidget(w)
                w.deleteLater()
            if not self._frames:
                self._view.clear_image()
                self._pos_lbl.setText("Sin frames")
                self._prev_btn.setEnabled(False)
                self._next_btn.setEnabled(False)
                self._del_frame_btn.setEnabled(False)
                return
            next_idx = min(self._current, len(self._frames) - 1)
            self._show_frame(next_idx)
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")

    def _delete_current_folder(self) -> None:
        if self._current_dir is None:
            return
        name = self._current_dir.name
        answer = QMessageBox.question(
            self, "Borrar carpeta completa",
            f"¿Borrar la carpeta completa '{name}' con todos sus frames?\n"
            f"Esta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(self._current_dir)
            self._frames.clear()
            self._current_dir = None
            self._clear_thumbs()
            self._view.clear_image()
            self._pos_lbl.setText("Sin frames")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            self._del_frame_btn.setEnabled(False)
            self._del_folder_btn.setEnabled(False)
            self._info_lbl.setText("Carpeta eliminada")
            self.folder_deleted.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")

    def _go_prev(self) -> None:
        self._show_frame(self._current - 1)

    def _go_next(self) -> None:
        self._show_frame(self._current + 1)

    def _go_to(self, idx: int) -> None:
        self._show_frame(idx)


# ── Visor principal ──────────────────────────────────────────────────────────

class OperatorFrameViewer(QMainWindow):
    """
    Visor de frames para el operario.

    Pestañas:
      • Paradas — eventos de parada de línea con evidencia pre/post
      • OK recientes — buffer circular de las últimas inspecciones OK
    """

    def __init__(self, system, parent=None):
        super().__init__(parent)
        self._system = system
        self._mode = "events"   # "events" | "ok" | "nok" | "timeline"
        self._summaries: list[dict] = []
        self._current_summary: Optional[dict] = None

        self.setWindowTitle("Visor de Frames — DEFYVISION")
        icon_pix = QPixmap(str(_ROOT / "logos" / "logo_ventana.jpg"))
        if not icon_pix.isNull():
            self.setWindowIcon(QIcon(icon_pix))
        self.setStyleSheet(f"background:{_BG};")
        self.resize(1280, 780)
        self._build_ui()

    # ── Construcción de UI ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background:{_BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────
        root.addWidget(self._build_header())

        # ── Cuerpo principal: lista + visor ───────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background:{_BORDER}; width:2px; }}"
        )

        # Lista izquierda
        self._list_widget = QListWidget()
        self._list_widget.setStyleSheet(
            f"QListWidget {{ background:{_PANEL}; border:none; outline:none; }}"
            f"QListWidget::item {{ border-bottom:1px solid {_BORDER}; padding:2px; }}"
            f"QListWidget::item:selected {{ background:#1e3a5f; border-left:3px solid {_ACCENT}; }}"
            "QScrollBar:vertical { width:6px; background:#0d1117; }"
            "QScrollBar::handle:vertical { background:#30363d; border-radius:3px; }"
        )
        self._list_widget.setMinimumWidth(230)
        self._list_widget.setMaximumWidth(320)
        self._list_widget.currentRowChanged.connect(self._on_list_select)
        splitter.addWidget(self._list_widget)

        # Panel de visor derecho
        self._nav_panel = _EventNavPanel()
        self._nav_panel.folder_deleted.connect(self.reload)
        splitter.addWidget(self._nav_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet(
            f"background:{_PANEL}; border-radius:8px; border:1px solid {_BORDER};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        title = QLabel("VISOR DE FRAMES")
        title.setStyleSheet(
            f"color:{_TEXT};font-size:15px;font-weight:700;letter-spacing:3px;"
            "background:transparent;border:none;"
        )
        lay.addWidget(title)
        lay.addStretch()

        def _tab_btn(label, mode, color):
            b = QPushButton(label)
            b.setFixedHeight(30)
            b.setMinimumWidth(130)
            b.setCheckable(True)
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{_MUTED}; "
                f"border:1px solid {_BORDER}; border-radius:6px; font-size:11px; "
                f"font-weight:600; padding:0 12px; }}"
                f"QPushButton:checked {{ background:{color}; color:#ffffff; border-color:{color}; }}"
                f"QPushButton:hover:!checked {{ background:{color}22; border-color:{color}; }}"
            )
            b.clicked.connect(lambda: self._switch_mode(mode))
            return b

        self._events_btn   = _tab_btn("⚠  Paradas de línea",    "events",   _NOK_CLR)
        self._ok_btn       = _tab_btn("✓  Frames OK recientes",  "ok",       _OK_CLR)
        self._nok_btn      = _tab_btn("✗  Frames NOK recientes", "nok",      _WARN)
        self._timeline_btn = _tab_btn("≡  Flujo cronológico",    "timeline", _ACCENT)

        lay.addWidget(self._events_btn)
        lay.addWidget(self._ok_btn)
        lay.addWidget(self._nok_btn)
        lay.addWidget(self._timeline_btn)

        reload_btn = QPushButton("↺  Recargar")
        reload_btn.setFixedHeight(30)
        reload_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{_MUTED}; "
            f"border:1px solid {_BORDER}; border-radius:6px; font-size:11px; padding:0 10px; }}"
            f"QPushButton:hover {{ color:{_TEXT}; border-color:{_TEXT}; }}"
        )
        reload_btn.clicked.connect(self.reload)
        lay.addWidget(reload_btn)

        _del_ss = (
            "QPushButton {{ background:transparent; color:#f87171; "
            "border:1px solid #7f1d1d; border-radius:6px; font-size:11px; "
            "font-weight:600; padding:0 10px; }}"
            "QPushButton:hover {{ background:#7f1d1d; color:#ffffff; border-color:#dc2626; }}"
            "QPushButton:disabled {{ color:#374151; border-color:#1f2937; }}"
        )
        self._del_sel_btn = QPushButton("✕  Borrar selección")
        self._del_sel_btn.setFixedHeight(30)
        self._del_sel_btn.setEnabled(False)
        self._del_sel_btn.setStyleSheet(_del_ss)
        self._del_sel_btn.clicked.connect(self._delete_selected)
        lay.addWidget(self._del_sel_btn)

        self._del_all_btn = QPushButton("✕✕  Borrar todo")
        self._del_all_btn.setFixedHeight(30)
        self._del_all_btn.setStyleSheet(_del_ss)
        self._del_all_btn.clicked.connect(self._delete_all)
        lay.addWidget(self._del_all_btn)

        self._disk_lbl = QLabel("")
        self._disk_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:10px;background:transparent;border:none;"
        )
        lay.addWidget(self._disk_lbl)

        return bar

    # ── Modos ─────────────────────────────────────────────────────────

    def _switch_mode(self, mode: str) -> None:
        self._mode = mode
        self._events_btn.setChecked(mode == "events")
        self._ok_btn.setChecked(mode == "ok")
        self._nok_btn.setChecked(mode == "nok")
        self._timeline_btn.setChecked(mode == "timeline")
        self._populate_list()

    def reload(self) -> None:
        self._populate_list()

    def _update_disk_label(self) -> None:
        try:
            # Presupuesto configurado
            tol_path = _ROOT / "config" / "tolerancias.yaml"
            max_gb = 10.0
            try:
                tols = yaml.safe_load(tol_path.read_text(encoding="utf-8"))
                max_gb = float(tols.get("events_max_disk_gb", 10.0))
            except Exception:
                pass

            ev_bytes = sum(f.stat().st_size for f in _EVENTS_DIR.rglob("*") if f.is_file()) \
                       if _EVENTS_DIR.exists() else 0
            ev_gb   = ev_bytes / 1_073_741_824
            pct     = min(100.0, ev_gb / max_gb * 100) if max_gb > 0 else 0.0
            free_gb = max(0.0, max_gb - ev_gb)

            bar_total = 16
            bar_fill  = int(round(pct / 100 * bar_total))
            bar = "█" * bar_fill + "░" * (bar_total - bar_fill)

            color = _OK_CLR if pct < 70 else (_WARN if pct < 90 else _NOK_CLR)
            self._disk_lbl.setText(
                f"💾  Buffer evidencias:  {ev_gb:.2f} / {max_gb:.0f} GB  [{bar}]  {pct:.0f}%  ({free_gb:.2f} GB libres)"
            )
            self._disk_lbl.setStyleSheet(
                f"color:{color};font-size:10px;background:transparent;border:none;"
            )
        except Exception:
            pass

    def _populate_list(self) -> None:
        self._update_disk_label()
        self._list_widget.clear()

        if self._mode == "events":
            self._populate_events()
        elif self._mode == "nok":
            self._populate_nok()
        elif self._mode == "timeline":
            self._populate_timeline()
        else:
            self._populate_ok()

    def _populate_events(self) -> None:
        """Lee data/events/ y llena la lista con los eventos encontrados."""
        if not _EVENTS_DIR.exists():
            item = QListWidgetItem("Sin eventos grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        dirs = sorted(
            (d for d in _EVENTS_DIR.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not dirs:
            item = QListWidgetItem("Sin eventos grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        self._summaries = [_event_summary(d) for d in dirs]

        for s in self._summaries:
            widget = self._make_event_card(s)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, widget)

    def _make_event_card(self, s: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)

        ts = _format_ts(s["timestamp"])
        date_lbl = QLabel(ts)
        date_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:11px;font-weight:700;background:transparent;"
        )
        lay.addWidget(date_lbl)

        scanner = _scanner_display(s["scanner_id"])
        ev_type = s["event_type"].replace("_", " ").upper() if s["event_type"] else "PARADA"
        ev_color = _NOK_CLR if "stop" in s["event_type"] else _WARN
        meta_lbl = QLabel(f"{scanner}  ·  {ev_type}")
        meta_lbl.setStyleSheet(
            f"color:{ev_color};font-size:10px;font-weight:600;background:transparent;"
        )
        lay.addWidget(meta_lbl)

        reason = s["reason"]
        if reason:
            r_lbl = QLabel(reason)
            r_lbl.setWordWrap(True)
            r_lbl.setStyleSheet(
                f"color:{_MUTED};font-size:9px;background:transparent;"
            )
            lay.addWidget(r_lbl)

        total = len(s["all_frames"])
        size_kb = s["total_bytes"] // 1024 if s["total_bytes"] else 0
        count_lbl = QLabel(
            f"{s['pre_count']} pre  +  {s['post_count']} post  ·  {size_kb} KB"
        )
        count_lbl.setStyleSheet(
            f"color:{_MUTED};font-size:9px;background:transparent;"
        )
        lay.addWidget(count_lbl)

        return w

    def _populate_ok(self) -> None:
        """Lee data/output/ok_buffer/ y llena la lista con los scanners disponibles."""
        self._summaries = []
        if not _OK_BUF_BASE.exists():
            item = QListWidgetItem("Sin frames OK grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        scanner_dirs = sorted(d for d in _OK_BUF_BASE.iterdir() if d.is_dir())
        if not scanner_dirs:
            item = QListWidgetItem("Sin frames OK grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        for sdir in scanner_dirs:
            frames = sorted(
                (f for f in sdir.glob("ok_*.jpg") if "_raw" not in f.name),
                key=lambda f: f.stat().st_mtime,
            )
            if not frames:
                continue
            summary = {
                "type": "ok",
                "scanner_id": sdir.name,
                "frames": frames,
                "dir": sdir,
            }
            self._summaries.append(summary)

            widget = self._make_ok_card(summary)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, widget)

    def _make_ok_card(self, s: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(3)

        scanner = _scanner_display(s["scanner_id"])
        title = QLabel(scanner)
        title.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:700;background:transparent;"
        )
        lay.addWidget(title)

        n = len(s["frames"])
        count_lbl = QLabel(f"{n} frames OK guardados")
        count_lbl.setStyleSheet(
            f"color:{_OK_CLR};font-size:10px;font-weight:600;background:transparent;"
        )
        lay.addWidget(count_lbl)

        return w

    def _populate_nok(self) -> None:
        """Lee data/output/nok/ y agrupa los overlays NOK por scanner."""
        self._summaries = []
        if not _NOK_DIR.exists():
            item = QListWidgetItem("Sin frames NOK grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        # Agrupar archivos PNG por scanner (prefijo "scanner_X_" del nombre)
        files = sorted(_NOK_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            item = QListWidgetItem("Sin frames NOK grabados")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        # Agrupar por scanner_id
        groups: dict[str, list[Path]] = {}
        for f in files:
            m = re.match(r"^(scanner_\w+)_", f.name)
            sid = m.group(1) if m else "unknown"
            groups.setdefault(sid, []).append(f)

        for sid, frames in sorted(groups.items()):
            summary = {"type": "nok", "scanner_id": sid, "frames": frames}
            self._summaries.append(summary)
            widget = self._make_nok_card(summary)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, widget)

    def _make_nok_card(self, s: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(3)

        scanner = _scanner_display(s["scanner_id"])
        title = QLabel(scanner)
        title.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:700;background:transparent;"
        )
        lay.addWidget(title)

        n = len(s["frames"])
        count_lbl = QLabel(f"{n} frames NOK guardados")
        count_lbl.setStyleSheet(
            f"color:{_WARN};font-size:10px;font-weight:600;background:transparent;"
        )
        lay.addWidget(count_lbl)

        return w

    def _populate_timeline(self) -> None:
        """Lee data/output/timeline/ y muestra el flujo cronológico por scanner."""
        self._summaries = []
        if not _TIMELINE_BASE.exists():
            item = QListWidgetItem("Sin flujo grabado aún")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        scanner_dirs = sorted(d for d in _TIMELINE_BASE.iterdir() if d.is_dir())
        if not scanner_dirs:
            item = QListWidgetItem("Sin flujo grabado aún")
            item.setForeground(Qt.GlobalColor.gray)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list_widget.addItem(item)
            return

        for sd in scanner_dirs:
            files = sorted(
                (f for f in sd.iterdir() if f.suffix.lower() == ".jpg"),
                key=lambda f: f.stat().st_mtime,
            )
            if not files:
                continue
            counts: dict[str, int] = {}
            for f in files:
                tag = f.stem.split("_", 1)[1] if "_" in f.stem else "OK"
                counts[tag] = counts.get(tag, 0) + 1
            summary = {
                "type": "timeline",
                "scanner_id": sd.name,
                "frames": files,
                "counts": counts,
                "dir": sd,
            }
            self._summaries.append(summary)
            widget = self._make_timeline_card(summary)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, widget)

        # Auto-seleccionar el primero
        if self._summaries:
            self._list_widget.setCurrentRow(0)

    def _make_timeline_card(self, s: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(3)

        scanner = _scanner_display(s["scanner_id"])
        title = QLabel(scanner)
        title.setStyleSheet(
            f"color:{_TEXT};font-size:12px;font-weight:700;background:transparent;"
        )
        lay.addWidget(title)

        total = len(s["frames"])
        counts = s.get("counts", {})
        ok_n    = counts.get("OK",   0)
        nok_n   = counts.get("NOK",  0)
        stop_n  = counts.get("STOP", 0)
        lq_n    = counts.get("LQ",   0)

        total_lbl = QLabel(f"{total} frames en orden cronológico")
        total_lbl.setStyleSheet(
            f"color:{_ACCENT};font-size:10px;font-weight:600;background:transparent;"
        )
        lay.addWidget(total_lbl)

        detail_parts = []
        if ok_n:   detail_parts.append(f"{ok_n} OK")
        if nok_n:  detail_parts.append(f"{nok_n} NOK")
        if stop_n: detail_parts.append(f"{stop_n} STOP")
        if lq_n:   detail_parts.append(f"{lq_n} LQ")
        if detail_parts:
            detail_lbl = QLabel("  ·  ".join(detail_parts))
            detail_lbl.setStyleSheet(
                f"color:{_MUTED};font-size:9px;background:transparent;"
            )
            lay.addWidget(detail_lbl)

        return w

    # ── Selección ─────────────────────────────────────────────────────

    def _on_list_select(self, row: int) -> None:
        if row < 0 or row >= len(self._summaries):
            self._current_summary = None
            self._del_sel_btn.setEnabled(False)
            return
        s = self._summaries[row]
        self._current_summary = s
        self._del_sel_btn.setEnabled(True)
        if self._mode == "events":
            self._nav_panel.load_event(s)
        elif self._mode == "nok":
            scanner_label = _scanner_display(s["scanner_id"])
            self._nav_panel.load_ok_buffer(s["frames"], scanner_label, folder_dir=None)
        elif self._mode == "timeline":
            scanner_label = _scanner_display(s["scanner_id"])
            self._nav_panel.load_timeline(s["frames"], scanner_label)
        else:
            scanner_label = _scanner_display(s["scanner_id"])
            self._nav_panel.load_ok_buffer(s["frames"], scanner_label,
                                            folder_dir=s.get("dir"))

    # ── Borrado ───────────────────────────────────────────────────────

    def _delete_selected(self) -> None:
        s = self._current_summary
        if s is None:
            return
        if self._mode == "events":
            name = s.get("name", str(s.get("dir", "")))
            answer = QMessageBox.question(
                self, "Borrar evidencia",
                f"¿Borrar la carpeta completa '{name}'?\nEsta acción no se puede deshacer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(s["dir"])
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")
                return
        elif self._mode == "ok":
            scanner = _scanner_display(s["scanner_id"])
            answer = QMessageBox.question(
                self, "Borrar frames OK",
                f"¿Borrar todos los frames OK de {scanner}?\nEsta acción no se puede deshacer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                for f in s["frames"]:
                    f.unlink(missing_ok=True)
                for suffix in ("_raw.jpg",):
                    for f in s["dir"].glob(f"ok_*{suffix}"):
                        f.unlink(missing_ok=True)
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")
                return
        elif self._mode == "nok":
            scanner = _scanner_display(s["scanner_id"])
            answer = QMessageBox.question(
                self, "Borrar frames NOK",
                f"¿Borrar todos los frames NOK de {scanner}?\nEsta acción no se puede deshacer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                for f in s["frames"]:
                    f.unlink(missing_ok=True)
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"No se pudo borrar:\n{exc}")
                return
        self._current_summary = None
        self._del_sel_btn.setEnabled(False)
        self._populate_list()

    def _delete_all(self) -> None:
        if self._mode == "events":
            label = "todas las evidencias de parada"
        elif self._mode == "ok":
            label = "todos los frames OK guardados"
        else:
            label = "todos los frames NOK guardados"
        answer = QMessageBox.question(
            self, "Borrar todo",
            f"¿Borrar {label}?\nEsta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._mode == "events":
                if _EVENTS_DIR.exists():
                    for d in _EVENTS_DIR.iterdir():
                        if d.is_dir():
                            shutil.rmtree(d)
            elif self._mode == "ok":
                if _OK_BUF_BASE.exists():
                    for d in _OK_BUF_BASE.iterdir():
                        if d.is_dir():
                            for f in d.glob("ok_*.jpg"):
                                f.unlink(missing_ok=True)
            elif self._mode == "nok":
                if _NOK_DIR.exists():
                    for f in _NOK_DIR.iterdir():
                        if f.is_file():
                            f.unlink(missing_ok=True)
                        elif f.is_dir():
                            shutil.rmtree(f, ignore_errors=True)
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo borrar todo:\n{exc}")
            return
        self._current_summary = None
        self._del_sel_btn.setEnabled(False)
        self._populate_list()

    # ── showEvent: activar modo eventos por defecto ───────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._events_btn.setChecked(self._mode == "events")
        self._ok_btn.setChecked(self._mode == "ok")
        self._nok_btn.setChecked(self._mode == "nok")
        self._populate_list()
