import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.inspection import inspect_image

for name in ("frame_0162", "frame_0172", "frame_0182", "frame_0186"):
    p = Path(rf"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\{name}.png")
    r = inspect_image("modelo_A", p, scanner_id="scanner_2")
    print(f"{name}: tilt={r.sheet_tilt_deg:+.2f}  warn={r.tilt_warn}  missing={r.report.missing}  status={r.status}")
