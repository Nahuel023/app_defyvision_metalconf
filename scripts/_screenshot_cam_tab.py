"""Toma screenshot del tab Camara para revision visual."""
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication, QMainWindow
from unittest.mock import MagicMock

app = QApplication(sys.argv)
from src.ui.service import CameraCalibTab

sm = MagicMock()
sm.scanner_ids.return_value = ["scanner_1", "scanner_2"]
cm = MagicMock()
cm.is_running = False
sm.camera.return_value = cm

win = QMainWindow()
win.setFixedSize(1280, 900)
tab = CameraCalibTab(sm)
win.setCentralWidget(tab)
# Evitar auto-connect durante el test
tab._ip_manual_disc = [True, True]
win.show()
app.processEvents()
app.processEvents()

out = "data/debug_cam_tab.png"
win.grab().save(out)
print(f"saved to {out}")
