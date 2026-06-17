"""Debug: imprime offset del pattern alignment para frames 68-75 de scanner_1."""
import sys
from pathlib import Path

import src.pipeline.edge_centering as ec

_orig = ec.compute_centering

def _patched(img_bgr, holes, **kw):
    r = _orig(img_bgr, holes, **kw)
    if r is not None:
        slope = getattr(r, "pattern_sheet_slope_delta_max_deg", 0.0)
        print(f"  [centering] offset_px={r.offset_px:.2f}  slope_delta={slope:.2f}deg")
    return r

ec.compute_centering = _patched

from src.inspection import inspect_image

frames_dir = Path(r"C:\Users\DefyC\Downloads\IMAGENES-VIERNES-12-EDITADAS\12-06-2026-MICROPERFORADO_10_SCANNER_1")
targets = [f"frame_{i:04d}.png" for i in range(66, 76)]

for fn in targets:
    p = frames_dir / fn
    if not p.exists():
        print(f"{fn}: NOT FOUND")
        continue
    r = inspect_image("modelo_B", p, scanner_id="scanner_1")
    ms = getattr(r, "machine_stop", False)
    cent = getattr(r, "centering", None)
    off = f"{cent.offset_px:.2f}" if cent and hasattr(cent, "offset_px") else "None"
    print(f"{fn}: raw={r.status}  machine_stop={ms}  centering.offset={off}")
