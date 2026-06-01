"""Show where missing points fall (esp. top rows) and pattern top cells."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
from src.inspection import inspect_image
from src.patterns.pattern_io import load_pattern, find_pattern_path

MODEL, SCANNER = "modelo_A", "scanner_2"
OUT = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data"
frames = sys.argv[1:] or [
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0162.png",
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0140.png",
    r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0199.png",
]
pat = load_pattern(find_pattern_path(MODEL, SCANNER))
cells = pat.cells
cjs = sorted(set(cj for _, cj in cells))
print(f"pattern cells={len(cells)}  cj range {cjs[0]}..{cjs[-1]}")
top2 = cjs[:2]
for cj in top2:
    cis = sorted(ci for ci, c in cells if c == cj)
    print(f"  TOP row cj={cj}: ci={cis}")
# pattern point y for top rows
ys_top = sorted(p[1] for p, (_, cj) in zip(pat.points, cells) if cj in top2)
print(f"  top-row pattern y: {[round(y) for y in ys_top]}")

for f in frames:
    r = inspect_image(MODEL, Path(f), scanner_id=SCANNER)
    miss = r.report.missing_points
    miss_top = [(round(x), round(y)) for (x, y) in miss if y < 80]
    print(f"\n{Path(f).stem}: missing={len(miss)}  status={r.status}")
    print(f"  missing y<80 (TOP): {miss_top}")
    print(f"  all missing (x,y): {[(round(x),round(y)) for x,y in miss]}")
    # save ROI-space top crop of overlay (overlay is full-frame; ROI x=870..1250)
    ov = r.overlay
    crop = ov[0:160, 850:1270]
    cv2.imwrite(OUT + rf"\debug_top_{Path(f).stem}.png",
                cv2.resize(crop, (crop.shape[1]*2, crop.shape[0]*2), interpolation=cv2.INTER_NEAREST))
print("\nsaved debug_top_*.png")
