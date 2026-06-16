"""Diálogo de autenticación para acceso al Modo Servicio."""

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

_DARK   = "#0f172a"
_PANEL  = "#1e293b"
_TEXT   = "#f1f5f9"
_MUTED  = "#94a3b8"
_BORDER = "#334155"
_ACCENT = "#38bdf8"


def service_login_enabled() -> bool:
    """Return whether service-mode login should be shown."""
    try:
        with open("config/app.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        service_cfg = cfg.get("service", {}) or {}
        return bool(service_cfg.get("login_enabled", True))
    except Exception:
        return True


class LoginDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modo Servicio — Autenticación")
        self.setFixedSize(400, 280)
        self.setModal(True)
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._creds = self._load_creds()
        self._build_ui()

    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Evitar que Escape cierre el diálogo accidentalmente
        if event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)

    def _load_creds(self) -> dict:
        try:
            with open("config/app.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("service", {"username": "servicio", "password": "1234"})
        except Exception:
            return {"username": "servicio", "password": "1234"}

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background:{_DARK};color:{_TEXT};")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 22)
        root.setSpacing(18)

        title = QLabel("Acceso Modo Servicio")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size:18px;font-weight:700;color:{_ACCENT};"
            "background:transparent;"
        )
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        lbl_style   = f"color:{_MUTED};font-size:15px;"
        field_style = (
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 8px;font-size:15px;"
        )

        user_lbl = QLabel("Usuario:")
        user_lbl.setStyleSheet(lbl_style)
        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("Usuario")
        self._user_edit.setStyleSheet(field_style)
        # Enter en usuario mueve el foco a contraseña
        self._user_edit.returnPressed.connect(self._pass_edit_focus)
        form.addRow(user_lbl, self._user_edit)

        pass_lbl = QLabel("Contraseña:")
        pass_lbl.setStyleSheet(lbl_style)

        # Fila: campo contraseña + botón ojo
        pass_row = QHBoxLayout()
        pass_row.setSpacing(4)

        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_edit.setPlaceholderText("Contraseña")
        self._pass_edit.setStyleSheet(field_style)
        self._pass_edit.returnPressed.connect(self._on_accept)
        pass_row.addWidget(self._pass_edit)

        self._eye_btn = QPushButton("👁")
        self._eye_btn.setFixedSize(32, 32)
        self._eye_btn.setCheckable(True)
        self._eye_btn.setToolTip("Mostrar / ocultar contraseña")
        self._eye_btn.setStyleSheet(
            f"QPushButton{{background:{_PANEL};color:{_MUTED};"
            f"border:1px solid {_BORDER};border-radius:5px;font-size:14px;}}"
            f"QPushButton:checked{{color:{_ACCENT};border-color:{_ACCENT};}}"
            f"QPushButton:hover{{background:#243044;}}"
        )
        self._eye_btn.toggled.connect(self._toggle_echo)
        pass_row.addWidget(self._eye_btn)

        form.addRow(pass_lbl, pass_row)
        root.addLayout(form)

        self._error_lbl = QLabel("")
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.setStyleSheet(
            "color:#f87171;font-size:11px;background:transparent;"
        )
        self._error_lbl.hide()
        root.addWidget(self._error_lbl)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setStyleSheet(
            "background:#475569;color:white;border-radius:5px;"
            "font-size:12px;padding:0 16px;border:none;"
        )
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("Ingresar")
        ok_btn.setFixedHeight(32)
        ok_btn.setStyleSheet(
            "background:#1d4ed8;color:white;border-radius:5px;"
            "font-size:12px;font-weight:700;padding:0 20px;border:none;"
        )
        ok_btn.clicked.connect(self._on_accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _pass_edit_focus(self) -> None:
        self._pass_edit.setFocus()

    def _toggle_echo(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self._pass_edit.setEchoMode(mode)

    def _on_accept(self) -> None:
        user = self._user_edit.text().strip()
        pwd  = self._pass_edit.text()
        if (user == self._creds.get("username", "")
                and pwd == self._creds.get("password", "")):
            self.accept()
        else:
            self._error_lbl.setText("Usuario o contraseña incorrectos.")
            self._error_lbl.show()
            self._pass_edit.clear()
            self._pass_edit.setFocus()
