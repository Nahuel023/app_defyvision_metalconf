"""
Diagnostic script: print pattern_zigzag_std_px, pattern_zigzag_max_px, and slope delta
for each frame in the folder, to help tune pattern_align_std_max_px and pattern_slope_delta_max_deg.
Usage: .venv/Scripts/python.exe scripts/diag_frames.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inspection import inspect_image
from src.utils.config import load_tolerances
from src.patterns.pattern_io import load_pattern
from src.patterns.roi import load_roi

MODEL = "modelo_B"
SCANNER = "scanner_1"
FOLDER = Path(r"C:\Users\DefyC\Downloads\10-06-2026-MICROPERFORADO\10-06-2026-MICROPERFORADO_5_SCANNER_1")

from src.patterns.pattern_io import find_pattern_path
tolerances = load_tolerances(MODEL)
pattern = load_pattern(find_pattern_path(MODEL, scanner_id=SCANNER))
roi = load_roi(MODEL, scanner_id=SCANNER)
pre = {
    "tolerances": tolerances,
    "pattern": pattern,
    "roi": roi,
    "ema_state": {},
    "desalign_state": {"streak": 0, "reason": ""},
}

frames = sorted(FOLDER.glob("frame_*.png"))
print(f"{'Frame':<15} {'zigzag_std':>12} {'zigzag_max':>12} {'slope_delta':>12} {'missing':>8} {'status':<6}")
print("-" * 70)

for fp in frames:
    result = inspect_image(MODEL, fp, scanner_id=SCANNER, _preloaded=pre)
    c = result.centering
    slope = getattr(c, "pattern_sheet_slope_delta_max_deg", 0.0) if c else 0.0
    print(
        f"{fp.name:<15} "
        f"{result.pattern_zigzag_std_px:>12.2f} "
        f"{result.pattern_zigzag_max_px:>12.2f} "
        f"{slope:>12.2f} "
        f"{result.report.missing:>8} "
        f"{result.status:<6}"
    )
