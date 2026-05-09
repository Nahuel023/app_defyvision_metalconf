import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inspection import inspect_image

frames_dir = Path("data/frames")
for p in sorted(frames_dir.glob("*.png")):
    r = inspect_image("modelo_A", p)
    sx, sy = r.shift_xy if r.shift_xy else (0.0, 0.0)
    print(f"{p.name[-19:-4]}  angle={r.angle_deg:+.2f}  shift=({sx:+.1f},{sy:+.1f})  missing={len(r.report.missing_points):3d}  status={r.status}")
