import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from src.controller.system import InspectionSystem
from src.ui.service import RecordingTab

app = QApplication([])
sys_ = InspectionSystem()
tab = RecordingTab(sys_)
print("RecordingTab construido OK")
# Botones separados (no colisionan)
print("rec_stop is analyze_stop?:", tab._btn_stop is tab._btn_stop_analyze)
print("chip analisis texto inicial:", repr(tab._analyze_model_chip.text()))
# Cambiar modelo -> chip se actualiza
tab._on_model_btn_toggled("Microperforado", True)
print("tras Microperforado -> chip:", repr(tab._analyze_model_chip.text()))
tab._on_model_btn_toggled("Esterilla", True)
print("tras Esterilla -> chip:", repr(tab._analyze_model_chip.text()))
# Estado análisis
tab._set_analysis_running(True)
print("running -> stop_analyze:", tab._btn_stop_analyze.isEnabled(),
      "rec_stop:", tab._btn_stop.isEnabled(),
      "esterilla:", tab._btn_model_esterilla.isEnabled())
tab._set_analysis_running(False)
print("stopped -> stop_analyze:", tab._btn_stop_analyze.isEnabled())
