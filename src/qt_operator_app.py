import sys
import subprocess
import math
from pathlib import Path

import cv2
from PyQt6.QtCore import QObject, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPaintEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QHeaderView,
    QLineEdit,
)

from src.inspection import FolderInspectionSummary, TemporalFrameResult, inspect_folder


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


class LoadingSpinner(QWidget):
    def __init__(self, parent: QWidget | None = None, size: int = 22) -> None:
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(size, size)
        self.hide()

    def start(self) -> None:
        self._angle = 0
        self.show()
        self._timer.start(80)
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        if not self._timer.isActive():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = self.width() / 2
        center_y = self.height() / 2
        radius = min(self.width(), self.height()) * 0.33
        dot_radius = max(1.8, radius * 0.22)

        for index in range(12):
            angle_deg = self._angle - index * 30
            angle_rad = math.radians(angle_deg)
            x = center_x + math.cos(angle_rad) * radius
            y = center_y - math.sin(angle_rad) * radius
            color = QColor("#2563eb")
            color.setAlphaF(max(0.15, 1.0 - index * 0.075))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(x - dot_radius, y - dot_radius, dot_radius * 2, dot_radius * 2))


class FolderAnalysisWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, model: str, folder: Path, save_results: bool) -> None:
        super().__init__()
        self.model = model
        self.folder = folder
        self.save_results = save_results

    def run(self) -> None:
        try:
            summary = inspect_folder(self.model, self.folder, save=self.save_results)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(summary)


class OperatorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DEFYVISION Operador")
        self.resize(1500, 920)

        self.summary: FolderInspectionSummary | None = None
        self.current_folder = Path("data/frames")
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self.advance_playback)
        self.current_row = -1
        self.analysis_thread: QThread | None = None
        self.analysis_worker: FolderAnalysisWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        self.status_label = QLabel("SIN DATOS")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "background:#2b2b2b;color:white;font-size:30px;font-weight:700;padding:18px;border-radius:10px;"
        )
        root.addWidget(self.status_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        root.addLayout(controls)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(180)
        controls.addWidget(QLabel("Modelo"))
        controls.addWidget(self.model_combo)

        self.folder_edit = QLineEdit(str(self.current_folder))
        controls.addWidget(QLabel("Carpeta"))
        controls.addWidget(self.folder_edit, 1)

        self.browse_btn = QPushButton("Buscar")
        self.browse_btn.clicked.connect(self.choose_folder)
        controls.addWidget(self.browse_btn)

        self.analyze_btn = QPushButton("Analizar")
        self.analyze_btn.clicked.connect(self.analyze_folder)
        controls.addWidget(self.analyze_btn)

        self.save_checkbox = QCheckBox("Guardar resultados")
        self.save_checkbox.setChecked(True)
        controls.addWidget(self.save_checkbox)

        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.start_playback)
        self.play_btn.setEnabled(False)
        controls.addWidget(self.play_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_playback)
        self.stop_btn.setEnabled(False)
        controls.addWidget(self.stop_btn)

        self.refresh_btn = QPushButton("Refrescar modelos")
        self.refresh_btn.clicked.connect(self.refresh_models)
        controls.addWidget(self.refresh_btn)

        self.service_btn = QPushButton("Modo servicio")
        self.service_btn.clicked.connect(self.open_service_mode)
        controls.addWidget(self.service_btn)

        self.activity_label = QLabel("Listo para analizar")
        self.activity_label.setStyleSheet("color:#374151;font-size:13px;")
        controls.addWidget(self.activity_label)

        self.spinner = LoadingSpinner(self)
        self.statusBar().addPermanentWidget(self.spinner)
        self.statusBar().showMessage("Listo para analizar")

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(12)
        summary_grid.setVerticalSpacing(12)
        root.addLayout(summary_grid)

        self.total_card = self._build_metric_card("Frames", "0")
        self.raw_card = self._build_metric_card("Raw NOK", "0")
        self.temporal_card = self._build_metric_card("Temporal NOK", "0")
        self.response_card = self._build_metric_card("Respuesta", "-")
        summary_grid.addWidget(self.total_card[0], 0, 0)
        summary_grid.addWidget(self.raw_card[0], 0, 1)
        summary_grid.addWidget(self.temporal_card[0], 0, 2)
        summary_grid.addWidget(self.response_card[0], 0, 3)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        root.addWidget(content_splitter, 1)

        left_container = QWidget()
        left_panel = QVBoxLayout(left_container)
        left_panel.setSpacing(10)
        left_panel.setContentsMargins(0, 0, 0, 0)
        content_splitter.addWidget(left_container)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Frame", "Raw", "Temporal", "Racha NOK", "Missing", "Detected"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 188)
        for idx in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setStyleSheet("QTableWidget{font-size:13px;} QHeaderView::section{padding:4px 6px;}")
        self.table.itemSelectionChanged.connect(self.on_table_selection)
        left_panel.addWidget(self.table, 1)

        self.detail_label = QLabel("Sin frame seleccionado")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("font-size:12px;padding:6px 8px;background:#f4f4f5;border-radius:8px;")
        left_panel.addWidget(self.detail_label)

        right_container = QWidget()
        right_panel = QGridLayout(right_container)
        right_panel.setHorizontalSpacing(12)
        right_panel.setVerticalSpacing(6)
        right_panel.setContentsMargins(0, 0, 0, 0)
        content_splitter.addWidget(right_container)
        content_splitter.setStretchFactor(0, 2)
        content_splitter.setStretchFactor(1, 3)
        content_splitter.setSizes([520, 900])

        mask_title = QLabel("Mask")
        mask_title.setStyleSheet("font-size:12px;color:#d1d5db;padding:0 0 2px 2px;")
        overlay_title = QLabel("Overlay")
        overlay_title.setStyleSheet("font-size:12px;color:#d1d5db;padding:0 0 2px 2px;")
        right_panel.addWidget(mask_title, 0, 0)
        right_panel.addWidget(overlay_title, 0, 1)

        self.mask_label = QLabel()
        self.mask_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mask_label.setStyleSheet("background:#111827;border-radius:8px;")
        self.mask_label.setMinimumSize(420, 340)
        self.overlay_label = QLabel()
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setStyleSheet("background:#111827;border-radius:8px;")
        self.overlay_label.setMinimumSize(420, 340)
        right_panel.addWidget(self.mask_label, 1, 0)
        right_panel.addWidget(self.overlay_label, 1, 1)
        right_panel.setColumnStretch(0, 1)
        right_panel.setColumnStretch(1, 1)
        right_panel.setRowStretch(1, 1)

        self.refresh_models()

    def refresh_models(self) -> None:
        models_dir = Path("data/patterns")
        models = sorted(p.name for p in models_dir.iterdir() if p.is_dir()) if models_dir.exists() else []
        self.model_combo.clear()
        self.model_combo.addItems(models)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de frames", str(self.current_folder))
        if folder:
            self.current_folder = Path(folder)
            self.folder_edit.setText(folder)

    def analyze_folder(self) -> None:
        model = self.model_combo.currentText().strip()
        folder = Path(self.folder_edit.text().strip())
        if not model:
            QMessageBox.warning(self, "Modelo", "Selecciona un modelo.")
            return
        if not folder.exists():
            QMessageBox.warning(self, "Carpeta", "La carpeta seleccionada no existe.")
            return
        if not folder.is_dir():
            QMessageBox.warning(self, "Carpeta", "La ruta seleccionada no es una carpeta.")
            return
        if not any(path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES for path in folder.iterdir()):
            QMessageBox.information(
                self,
                "Carpeta vacia",
                "No se encontraron imagenes compatibles en la carpeta seleccionada.",
            )
            return
        if self.analysis_thread is not None:
            QMessageBox.information(self, "Analisis", "Ya hay un analisis en curso.")
            return

        self.stop_playback()
        self.summary = None
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.current_row = -1
        self.mask_label.clear()
        self.overlay_label.clear()
        self.detail_label.setText("Analizando carpeta...")

        self.analysis_thread = QThread(self)
        self.analysis_worker = FolderAnalysisWorker(
            model=model,
            folder=folder,
            save_results=self.save_checkbox.isChecked(),
        )
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.finished.connect(self._on_analysis_finished)
        self.analysis_worker.failed.connect(self._on_analysis_failed)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_thread.finished.connect(self._cleanup_analysis_thread)
        self._set_busy_state(True, f"Analizando {folder.name}...")
        self.analysis_thread.start()

    def open_service_mode(self) -> None:
        try:
            subprocess.Popen([sys.executable, "-m", "src.main", "gui"])
        except Exception as exc:
            QMessageBox.critical(self, "Modo servicio", str(exc))

    def _on_analysis_finished(self, summary: FolderInspectionSummary) -> None:
        self.summary = summary
        self.populate_summary(summary)
        self.populate_table(summary.temporal_results)
        if summary.temporal_results:
            self.table.selectRow(0)
            self.current_row = 0
        else:
            self.detail_label.setText("No se generaron resultados para mostrar.")
        self._set_busy_state(False, f"Analisis completado: {summary.total} frame(s)")

    def _on_analysis_failed(self, message: str) -> None:
        self._set_busy_state(False, "Error durante el analisis")
        self.detail_label.setText("No se pudo completar el analisis.")
        QMessageBox.critical(self, "Analizar carpeta", message)

    def _cleanup_analysis_thread(self) -> None:
        if self.analysis_worker is not None:
            self.analysis_worker.deleteLater()
        if self.analysis_thread is not None:
            self.analysis_thread.deleteLater()
        self.analysis_worker = None
        self.analysis_thread = None

    def _set_busy_state(self, is_busy: bool, message: str) -> None:
        self.model_combo.setEnabled(not is_busy)
        self.folder_edit.setEnabled(not is_busy)
        self.browse_btn.setEnabled(not is_busy)
        self.analyze_btn.setEnabled(not is_busy)
        self.refresh_btn.setEnabled(not is_busy)
        self.service_btn.setEnabled(not is_busy)
        self.save_checkbox.setEnabled(not is_busy)
        self.play_btn.setEnabled(not is_busy and self.summary is not None and bool(self.summary.temporal_results))
        self.stop_btn.setEnabled(not is_busy and self.summary is not None and bool(self.summary.temporal_results))
        self.activity_label.setText(message)
        if is_busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.statusBar().showMessage(message)

    def populate_summary(self, summary: FolderInspectionSummary) -> None:
        self.total_card[1].setText(str(summary.total))
        self.raw_card[1].setText(str(summary.nok))
        self.temporal_card[1].setText(str(summary.temporal_nok))
        response_text = f"{summary.response_time_sec:.2f}s"
        if not summary.meets_response_target:
            response_text += " fuera objetivo"
        self.response_card[1].setText(response_text)

        if summary.temporal_nok > 0:
            self._set_status("TEMPORAL NOK", "#b91c1c")
        else:
            self._set_status("TEMPORAL OK", "#166534")

    def populate_table(self, results: list[TemporalFrameResult]) -> None:
        self.table.setRowCount(len(results))
        for row, temporal in enumerate(results):
            result = temporal.result
            values = [
                result.image_path.name,
                result.status,
                temporal.decision_status,
                str(temporal.nok_streak),
                str(result.report.missing),
                str(result.report.detected),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor("#111827"))
                if col in (1, 2):
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

            if temporal.triggered:
                color = "#fecaca"
            elif temporal.decision_status == "NOK":
                color = "#fef3c7"
            else:
                color = "#dcfce7"
            for col in range(self.table.columnCount()):
                self.table.item(row, col).setBackground(QColor(color))

    def on_table_selection(self) -> None:
        if self.summary is None:
            return
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        self.current_row = row
        temporal = self.summary.temporal_results[row]
        result = temporal.result
        self.detail_label.setText(
            f"Frame: {result.image_path.name} | Raw: {result.status} | Temporal: {temporal.decision_status} | "
            f"Racha NOK: {temporal.nok_streak} | Missing: {result.report.missing} | Detected: {result.report.detected}"
        )
        if temporal.triggered:
            self._set_status("TEMPORAL NOK", "#b91c1c")
        elif temporal.decision_status == "OK":
            self._set_status("TEMPORAL OK", "#166534")
        elif result.status == "NOK":
            self._set_status("RAW NOK", "#c2410c")
        else:
            self._set_status("RAW OK", "#1d4ed8")
        self.show_image(self.mask_label, result.mask, max_width=420, max_height=300)
        self.show_image(self.overlay_label, result.overlay, max_width=420, max_height=300)

    def start_playback(self) -> None:
        if self.summary is None or not self.summary.temporal_results:
            QMessageBox.information(self, "Play", "Primero analiza una carpeta.")
            return

        interval_ms = 200
        if self.summary.frame_rate_hz > 0:
            interval_ms = max(50, int(1000 / self.summary.frame_rate_hz))
        self.playback_timer.start(interval_ms)
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def stop_playback(self) -> None:
        self.playback_timer.stop()
        if self.summary is not None and self.summary.temporal_results:
            self.play_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
        else:
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)

    def advance_playback(self) -> None:
        if self.summary is None or not self.summary.temporal_results:
            self.stop_playback()
            return

        next_row = self.current_row + 1
        if next_row >= len(self.summary.temporal_results):
            self.stop_playback()
            return

        self.table.selectRow(next_row)
        self.current_row = next_row

    def show_image(self, label: QLabel, image, max_width: int, max_height: int) -> None:
        available = label.contentsRect()
        width = max(max_width, available.width() - 12)
        height = max(max_height, available.height() - 12)
        pixmap = self._numpy_to_pixmap(image, max_width=width, max_height=height)
        label.setPixmap(pixmap)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_current_preview()

    def _refresh_current_preview(self) -> None:
        if self.summary is None:
            return
        if not (0 <= self.current_row < len(self.summary.temporal_results)):
            return
        result = self.summary.temporal_results[self.current_row].result
        self.show_image(self.mask_label, result.mask, max_width=520, max_height=420)
        self.show_image(self.overlay_label, result.overlay, max_width=520, max_height=420)

    def _numpy_to_pixmap(self, image, max_width: int, max_height: int) -> QPixmap:
        if image.ndim == 2:
            qimg = QImage(
                image.data,
                image.shape[1],
                image.shape[0],
                image.strides[0],
                QImage.Format.Format_Grayscale8,
            )
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            qimg = QImage(
                rgb.data,
                rgb.shape[1],
                rgb.shape[0],
                rgb.strides[0],
                QImage.Format.Format_RGB888,
            )
        pixmap = QPixmap.fromImage(qimg.copy())
        return pixmap.scaled(
            max_width,
            max_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"background:{color};color:white;font-size:30px;font-weight:700;padding:18px;border-radius:10px;"
        )

    def _build_metric_card(self, title: str, value: str) -> tuple[QWidget, QLabel]:
        card = QWidget()
        card.setStyleSheet("background:#f5f5f5;border-radius:10px;")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:12px;color:#52525b;")
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size:22px;font-weight:700;color:#111827;")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label


def launch_operator_ui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = OperatorWindow()
    window.show()
    app.exec()
