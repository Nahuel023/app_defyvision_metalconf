"""Inspect specific frames of a folder: tilt, missing (with positions), machine_stop.
Usage: _inspect_frames.py <folder> <frame_0078> <frame_0079> ...
Saves overlays to data/output/{ok,nok}.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.inspection import inspect_image

folder = Path(sys.argv[1])
names = sys.argv[2:]
for n in names:
    p = folder / (n if n.endswith(".png") else f"{n}.png")
    if not p.exists():
        print(f"{n}: NO EXISTE"); continue
    r = inspect_image("modelo_A", p, scanner_id="scanner_2", save=True)
    miss = [(round(x), round(y)) for (x, y) in r.report.missing_points]
    print(f"{p.stem}: tilt={r.sheet_tilt_deg:+.2f} warn={r.tilt_warn} "
          f"miss={r.report.missing} extra={r.report.extra} det={r.report.detected} "
          f"exp={r.report.expected} mstop={r.machine_stop} {r.status}")
    if 0 < r.report.missing <= 12:
        print(f"    missing pos: {miss}")
