import sys
import subprocess
from pathlib import Path

import cv2
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QHeaderView,
    QLineEdit,
)

from src.inspection import FolderInspectionSummary, TemporalFrameResult, inspect_folder


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

        browse_btn = QPushButton("Buscar")
        browse_btn.clicked.connect(self.choose_folder)
        controls.addWidget(browse_btn)

        analyze_btn = QPushButton("Analizar")
        analyze_btn.clicked.connect(self.analyze_folder)
        controls.addWidget(analyze_btn)

        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.start_playback)
        self.play_btn.setEnabled(False)
        controls.addWidget(self.play_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_playback)
        self.stop_btn.setEnabled(False)
        controls.addWidget(self.stop_btn)

        refresh_btn = QPushButton("Refrescar modelos")
        refresh_btn.clicked.connect(self.refresh_models)
        controls.addWidget(refresh_btn)

        service_btn = QPushButton("Modo servicio")
        service_btn.clicked.connect(self.open_service_mode)
        controls.addWidget(service_btn)

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

        content = QHBoxLayout()
        content.setSpacing(14)
        root.addLayout(content, 1)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)
        content.addLayout(left_panel, 3)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Frame", "Raw", "Temporal", "Racha NOK", "Missing", "Detected"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for idx in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.on_table_selection)
        left_panel.addWidget(self.table, 1)

        self.detail_label = QLabel("Sin frame seleccionado")
        self.detail_label.setStyleSheet("font-size:14px;padding:8px;background:#f4f4f5;border-radius:8px;")
        left_panel.addWidget(self.detail_label)

        right_panel = QGridLayout()
        right_panel.setHorizontalSpacing(12)
        right_panel.setVerticalSpacing(8)
        content.addLayout(right_panel, 2)

        right_panel.addWidget(QLabel("Mask"), 0, 0)
        right_panel.addWidget(QLabel("Overlay"), 0, 1)

        self.mask_label = QLabel()
        self.mask_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mask_label.setStyleSheet("background:#111827;border-radius:8px;")
        self.overlay_label = QLabel()
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setStyleSheet("background:#111827;border-radius:8px;")
        right_panel.addWidget(self.mask_label, 1, 0)
        right_panel.addWidget(self.overlay_label, 1, 1)

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
        try:
            self.summary = inspect_folder(model, folder, save=True)
        except Exception as exc:
            QMessageBox.critical(self, "Analizar carpeta", str(exc))
            return

        self.populate_summary(self.summary)
        self.populate_table(self.summary.temporal_results)
        if self.summary.temporal_results:
            self.play_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
            self.table.selectRow(0)
            self.current_row = 0
        else:
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)

    def open_service_mode(self) -> None:
        try:
            subprocess.Popen([sys.executable, "-m", "src.main", "gui"])
        except Exception as exc:
            QMessageBox.critical(self, "Modo servicio", str(exc))

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
        pixmap = self._numpy_to_pixmap(image, max_width=max_width, max_height=max_height)
        label.setPixmap(pixmap)

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
