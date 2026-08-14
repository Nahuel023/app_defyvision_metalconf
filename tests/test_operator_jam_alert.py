import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.ui.operator import MachineJamDialog, _is_machine_jam_reason


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_only_machine_jam_reason_requests_jam_screen() -> None:
    assert _is_machine_jam_reason(
        "Máquina trabada: material sin avanzar durante 22.0 s"
    )
    assert not _is_machine_jam_reason("Cámara sin señal durante 3.0 s")
    assert not _is_machine_jam_reason("3 imágenes NOK consecutivas")


def test_machine_jam_dialog_stays_open_until_operator_confirms() -> None:
    app = _app()
    dialog = MachineJamDialog(
        "SCANNER 1  ·  MICROPERFORADO",
        "Máquina trabada: material sin avanzar durante 22.0 s",
    )
    dialog.show()
    app.processEvents()

    dialog.reject()
    app.processEvents()
    assert dialog.isVisible()

    dialog.close()
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.findChild(type(dialog._confirm_btn), "jamConfirmButton") is not None

    dialog._confirm_btn.click()
    app.processEvents()
    assert not dialog.isVisible()
