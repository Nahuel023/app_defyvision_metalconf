import json
from pathlib import Path
p = json.loads(Path("data/patterns/scanner_2/modelo_A/holes.json").read_text())
print("image_size:", p.get("image_size"))
print("dx:", p.get("dx"), "dy:", p.get("dy"))
print("phase_x:", p.get("phase_x"), "phase_y:", p.get("phase_y"))
cells = p.get("cells", [])
print("cells count:", len(cells))
if cells:
    print("first 5 cells:", cells[:5])
pts = p.get("points", [])
print("points count:", len(pts))
if pts:
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    print(f"X range: {min(xs):.1f} - {max(xs):.1f}")
    print(f"Y range: {min(ys):.1f} - {max(ys):.1f}")
