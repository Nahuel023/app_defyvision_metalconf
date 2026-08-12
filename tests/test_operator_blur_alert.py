import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.ui.operator import BlurCleaningDialog, _is_blur_quality_reason


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_only_blur_reason_requests_lens_cleaning() -> None:
    assert _is_blur_quality_reason("Imagen borrosa: nitidez 89 (mínimo 255)")
    assert not _is_blur_quality_reason("Cámara sin señal durante 3.0 s")
    assert not _is_blur_quality_reason("Sin análisis durante 10.0 s")


def test_blur_dialog_stays_open_until_operator_confirms() -> None:
    app = _app()
    dialog = BlurCleaningDialog(
        "SCANNER 2  ·  ESTERILLA",
        "Imagen borrosa: nitidez 89 (mínimo 255)",
    )
    dialog.show()
    app.processEvents()

    dialog.reject()  # simula Escape
    app.processEvents()
    assert dialog.isVisible()

    dialog.close()  # simula intentar cerrar la ventana
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.findChild(type(dialog._confirm_btn), "blurConfirmButton") is not None

    dialog._confirm_btn.click()
    app.processEvents()
    assert not dialog.isVisible()
