"""Diálogo de autenticación para acceso al Modo Servicio."""

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

_DARK  = "#0f172a"
_PANEL = "#1e293b"
_TEXT  = "#f1f5f9"
_MUTED = "#94a3b8"
_BORDER = "#334155"
_ACCENT = "#38bdf8"


class LoginDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modo Servicio — Autenticación")
        self.setFixedSize(380, 210)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._creds = self._load_creds()
        self._build_ui()

    # ------------------------------------------------------------------

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
            f"font-size:15px;font-weight:700;color:{_ACCENT};"
            "background:transparent;"
        )
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        lbl_style = f"color:{_MUTED};font-size:12px;"
        field_style = (
            f"background:{_PANEL};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:5px;padding:5px 8px;font-size:13px;"
        )

        user_lbl = QLabel("Usuario:")
        user_lbl.setStyleSheet(lbl_style)
        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("Usuario")
        self._user_edit.setStyleSheet(field_style)
        form.addRow(user_lbl, self._user_edit)

        pass_lbl = QLabel("Contraseña:")
        pass_lbl.setStyleSheet(lbl_style)
        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_edit.setPlaceholderText("Contraseña")
        self._pass_edit.setStyleSheet(field_style)
        self._pass_edit.returnPressed.connect(self._on_accept)
        form.addRow(pass_lbl, self._pass_edit)

        root.addLayout(form)

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
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        user = self._user_edit.text().strip()
        pwd  = self._pass_edit.text()
        if (user == self._creds.get("username", "")
                and pwd == self._creds.get("password", "")):
            self.accept()
        else:
            QMessageBox.warning(self, "Acceso denegado",
                                "Usuario o contraseña incorrectos.")
            self._pass_edit.clear()
            self._pass_edit.setFocus()
