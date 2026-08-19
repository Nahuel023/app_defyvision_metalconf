import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

import src.ui.tolerance_window as tolerance_module
from src.ui.tolerance_window import ToleranceWindow, _PARAMS, _ScannerTolerancePanel


class _FakeIO:
    def __init__(self) -> None:
        self.cfg = {
            "model": "modelo_B",
            "allowed_models": ["modelo_A", "modelo_B"],
        }

    def scanner_config(self, _scanner_id: str) -> dict:
        return self.cfg


class _FakeScanner:
    def __init__(self) -> None:
        self.reload_count = 0

    def reload_cache(self) -> None:
        self.reload_count += 1


class _FakeSystem:
    def __init__(self) -> None:
        self.io = _FakeIO()
        self.fake_scanner = _FakeScanner()

    def scanner(self, _scanner_id: str) -> _FakeScanner:
        return self.fake_scanner

    def scanner_ids(self) -> list[str]:
        return ["scanner_2"]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_blur_threshold_is_exposed_with_operator_safe_range() -> None:
    blur = next(p for p in _PARAMS if p["key"] == "blur_score_min")
    assert blur["label"] == "Nitidez mínima"
    assert blur["vmin"] <= 175.0 <= blur["vmax"]


def test_operator_adjustments_do_not_expose_manual_roi() -> None:
    app = _app()
    window = ToleranceWindow(_FakeSystem())
    labels = {button.text() for button in window.findChildren(QPushButton)}

    assert window._roi_panel is None
    assert "Área de análisis" not in labels
    assert "GUARDAR ÁREA" not in labels

    # Incluso una llamada antigua que intente abrir la página 1 no puede crearla.
    window.show_page(1)
    app.processEvents()
    assert window._roi_panel is None
    window.close()


def test_selector_saves_selected_material_without_switching_active_model(
    monkeypatch,
) -> None:
    app = _app()
    system = _FakeSystem()
    saved: list[tuple[str, str, float]] = []

    monkeypatch.setattr(
        tolerance_module,
        "save_scanner_overrides",
        lambda scanner_id, updates, model=None: saved.append(
            (scanner_id, model, float(updates["blur_score_min"]))
        ),
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )

    panel = _ScannerTolerancePanel("scanner_2", system)
    panel._model_combo.setCurrentIndex(panel._model_combo.findData("modelo_A"))
    panel._spinboxes["blur_score_min"].setValue(175.0)
    panel._on_save()
    app.processEvents()

    assert saved == [("scanner_2", "modelo_A", 175.0)]
    assert system.io.cfg["model"] == "modelo_B"
    assert system.fake_scanner.reload_count == 0


def test_saving_active_material_reloads_without_changing_model(monkeypatch) -> None:
    app = _app()
    system = _FakeSystem()
    monkeypatch.setattr(
        tolerance_module, "save_scanner_overrides", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )

    panel = _ScannerTolerancePanel("scanner_2", system)
    panel._model_combo.setCurrentIndex(panel._model_combo.findData("modelo_B"))
    panel._on_save()
    app.processEvents()

    assert system.io.cfg["model"] == "modelo_B"
    assert system.fake_scanner.reload_count == 1
