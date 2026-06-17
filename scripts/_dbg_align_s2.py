"""Debug: centering metrics para scanner_2 frames 45-60."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inspection import inspect_image

frames_dir = Path(r"C:\Users\DefyC\Downloads\IMAGENES-VIERNES-12-EDITADAS\16-06-2026-MICROPERFORADO_1_SCANNER_2")
targets = [f"frame_{i:04d}.png" for i in range(45, 62)]

print(f"{'frame':12} {'raw':5} {'missing':8} {'zigzag_std':11} {'zigzag_max':11} {'slope_delta':11} {'offset_px':10} {'desalign':>9}")
print("-" * 90)
for fn in targets:
    p = frames_dir / fn
    if not p.exists():
        continue
    r = inspect_image("modelo_B", p, scanner_id="scanner_2")
    cent = getattr(r, "centering", None)
    if cent is not None:
        zstd = getattr(cent, "pattern_zigzag_std_px", float("nan"))
        zmax = getattr(cent, "pattern_zigzag_max_px", float("nan"))
        slope = getattr(cent, "pattern_sheet_slope_delta_max_deg", float("nan"))
        off = getattr(cent, "offset_px", float("nan"))
    else:
        zstd = zmax = slope = off = float("nan")
    miss = r.report.missing if hasattr(r, "report") else "?"
    pw = getattr(r, "pattern_alignment_warn", False)
    print(f"{fn:12} {r.status:5} {miss!s:8} {zstd:11.2f} {zmax:11.2f} {slope:11.2f} {off:10.2f} {str(pw):>9}")
